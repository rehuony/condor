"""Pydantic-based configuration for trading agents.

Mirrors the routines pattern: typed config with defaults, stored as config.yml
in the agent directory, editable via key=value messages or web UI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from condor.fsutil import atomic_write_text

# The execution modes that run a single tick as an experiment: no journal, the
# tick captured as a dry-run snapshot instead. The one definition of that set —
# AgentConfig.execution_mode below is the Literal naming every mode.
EXPERIMENT_MODES: frozenset[str] = frozenset({"dry_run", "run_once"})


def is_experiment_mode(mode: str) -> bool:
    """True for an execution mode that runs as an experiment (dry_run/run_once)."""
    return mode in EXPERIMENT_MODES


class RiskLimitsConfig(BaseModel):
    # A misspelled or unsupported safeguard must never look like active risk
    # protection. The runtime dataclass uses this same validator.
    model_config = ConfigDict(
        extra="forbid", strict=True, allow_inf_nan=False, hide_input_in_errors=True
    )

    max_position_size_quote: float = Field(
        default=500.0, ge=0, description="Max total position size in quote currency"
    )
    max_open_executors: int = Field(
        default=5, ge=0, description="Max simultaneous executors"
    )
    max_drawdown_pct: float = Field(
        default=-1.0,
        description="Session PnL decline from its recorded peak divided by current "
        "quote exposure, in percent, that pauses ticks; not account-equity "
        "drawdown or a daily-loss limit. -1 = disabled",
    )
    shutdown_drawdown_pct: float = Field(
        default=-1.0,
        description="Session PnL decline from its recorded peak divided by current "
        "quote exposure, in percent, that triggers emergency winddown "
        "(closes positions per shutdown.md); not account-equity drawdown. "
        "-1 = disabled",
    )
    max_drift_quote: float = Field(
        default=-1.0,
        description="Largest book-vs-venue drift (quote) this agent's own "
        "controllers may show before new exposure is refused; brakes always "
        "pass. -1 = disabled",
    )
    max_leverage: float = Field(
        default=-1.0,
        description="Most leverage a create may ask for, and the highest this "
        "session may set on an account. With it enabled, a create that "
        "declares no leverage is refused (the venue's own default applies to "
        "an omitted one). -1 = disabled",
    )

    @field_validator(
        "max_drawdown_pct", "shutdown_drawdown_pct", "max_drift_quote", "max_leverage"
    )
    @classmethod
    def disabled_or_nonnegative(cls, value: float) -> float:
        if value != -1 and value < 0:
            raise ValueError("must be -1 (disabled) or nonnegative")
        return value


class AgentConfig(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    server_name: str = Field(default="local", description="Hummingbot API server name")
    agent_key: str = Field(
        default="",
        description="LLM model to use (e.g. 'claude-code', 'ollama:llama3.1'). Empty = use strategy default.",
    )
    model_base_url: str = Field(
        default="",
        description="Custom base URL for OpenAI-compatible endpoints (LM Studio, vLLM). Leave empty for standard providers.",
    )
    total_amount_quote: float = Field(
        default=100.0,
        description="Total capital budget for this session in quote currency",
    )
    frequency_sec: int = Field(default=60, description="Tick frequency in seconds")
    tick_timeout_sec: int = Field(
        default=0,
        description="Wall-clock budget for one tick's agent session, in seconds; "
        "0 = use the runtime default (10 min, or CONDOR_TIMEOUT_TICK_DEFAULT)",
    )
    trading_context: str = Field(
        default="",
        description="Natural language session context that guides the agent's trading decisions",
    )
    execution_mode: Literal["dry_run", "run_once", "loop"] = Field(
        default="loop",
        description="Execution mode: dry_run (simulate), run_once (single live tick), loop (continuous)",
    )
    max_ticks: int = Field(
        default=0, description="Max ticks before auto-stop; 0 = unlimited"
    )
    restart_on_boot: bool = Field(
        default=False,
        description="Resume this loop after Condor restarts. The boot pass "
        "(condor.runtime.loops) marks an interrupted run and, only with this "
        "set, starts a FRESH session from the config as it stands then. Off by "
        "default: a trading loop that resumes unattended after a crash nobody "
        "noticed is a decision its owner has to make per strategy. Automatic "
        "restart is refused when drawdown limits are enabled: the new session "
        "cannot restore risk history. Review the previous session and positions "
        "before starting a new session manually.",
    )
    bot_name: str = Field(
        default="",
        description="If set, the agent operates this Hummingbot bot's controllers "
        "(deploy/retune) instead of creating standalone executors, and the bot's "
        "PnL is merged into the agent's reported performance. Empty = executor mode.",
    )
    bot_mode: Literal["auto", "executors", "bot"] = Field(
        default="auto",
        description="auto = controller mode iff bot_name is set (default); "
        "bot = controller mode on, deriving bot_name as {agent_slug}-{strategy_slug} "
        "when empty; executors = force executor mode even with a bot_name set.",
    )
    canvas_enabled: bool = Field(
        default=True,
        description="Keep a session canvas (the agent's running narrative) and "
        "publish a live session report. Costs ~1.2k input tokens per tick.",
    )
    canvas_nudge_ticks: int = Field(
        default=12,
        description="Remind the agent to revise its canvas after this many ticks "
        "without a revision",
    )
    canvas_band_usd: float = Field(
        default=25.0,
        description="PnL drift since the last canvas revision that triggers a "
        "revise-your-canvas nudge",
    )
    risk_limits: RiskLimitsConfig = Field(default_factory=RiskLimitsConfig)

    @model_validator(mode="after")
    def require_drawdown_history(self) -> AgentConfig:
        if self.execution_mode == "run_once" and (
            self.risk_limits.max_drawdown_pct >= 0
            or self.risk_limits.shutdown_drawdown_pct >= 0
        ):
            raise ValueError(
                "run_once cannot enforce drawdown limits because it has no session "
                "journal; use loop for live drawdown monitoring or dry_run to rehearse"
            )
        return self

    def to_engine_dict(self) -> dict[str, Any]:
        """Convert to the dict format expected by TickEngine."""
        d = self.model_dump()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentConfig:
        """Create from a raw dict (e.g. strategy.default_config)."""
        if not isinstance(d, dict):
            raise ValueError("Loop config must be a mapping")
        cleaned = {k: v for k, v in d.items() if k in cls.model_fields}
        # Translate dry_run shorthand → execution_mode
        if d.get("dry_run") and "execution_mode" not in d:
            cleaned["execution_mode"] = "dry_run"
        return cls(**cleaned)


def load_full_config(
    agent_dir: Path,
    defaults: dict[str, Any] | None = None,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load config preserving both AgentConfig fields and strategy-specific keys.

    Starts from strategy defaults, overlays saved config.yml and request
    overrides, then validates core fields and fills missing core defaults.
    """
    result = dict(defaults or {})

    config_path = agent_dir / "config.yml"
    if config_path.exists():
        try:
            saved = yaml.safe_load(config_path.read_text())
        except (OSError, yaml.YAMLError):
            # Parser errors quote source lines, which may carry private
            # strategy configuration. The API only needs the refusal.
            raise ValueError(
                "Cannot read saved loop config; repair config.yml before starting"
            ) from None
        if not isinstance(saved, dict):
            raise ValueError("Saved loop config must be a YAML mapping")
        result.update(saved)

    if overrides is not None:
        result.update(overrides)

    # Validate core fields and fill in any missing AgentConfig defaults
    core = AgentConfig.from_dict(result)
    core_defaults = core.model_dump()
    for k, v in core_defaults.items():
        result.setdefault(k, v)

    return result


def save_full_config(agent_dir: Path, config: dict[str, Any]) -> None:
    """Validate core safeguards, preserving strategy-specific fields in YAML."""
    AgentConfig.from_dict(config)
    config_path = agent_dir / "config.yml"
    agent_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        config_path, yaml.dump(config, default_flow_style=False, sort_keys=False)
    )
