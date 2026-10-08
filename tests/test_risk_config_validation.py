"""Risk safeguards must be supported and enforced before a loop is saved or run."""

from dataclasses import asdict
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from condor.agents.agent import AgentStore
from condor.agents.config import (
    AgentConfig,
    RiskLimitsConfig,
    load_full_config,
    save_full_config,
)
from condor.agents.risk import RiskEngine, RiskLimits
from condor.agents.strategy import StrategyStore
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import agents as routes


@pytest.mark.parametrize(
    "field",
    [
        "daily_loss",
        "daily_loss_pct",
        "max_consecutive_losses",
        "max_equity_drawdown_pct",
    ],
)
def test_unsupported_safeguards_are_rejected_by_both_config_and_runtime(field):
    with pytest.raises(ValueError, match=field):
        AgentConfig.from_dict({"risk_limits": {field: 2}})
    with pytest.raises(ValueError, match=field):
        RiskLimits.from_dict({field: 2})


def test_validation_errors_do_not_echo_supplied_values():
    with pytest.raises(ValueError) as error:
        AgentConfig.from_dict({"risk_limits": {"unsupported": "private-value"}})
    assert "unsupported" in str(error.value)
    assert "private-value" not in str(error.value)


@pytest.mark.parametrize(
    "limits",
    [
        {"max_position_size_quote": float("nan")},
        {"max_position_size_quote": float("inf")},
        {"max_position_size_quote": -1},
        {"max_position_size_quote": True},
        {"max_open_executors": -1},
        {"max_open_executors": 1.5},
        {"max_open_executors": True},
        {"max_drawdown_pct": -2},
        {"shutdown_drawdown_pct": -0.5},
        {"max_drift_quote": float("nan")},
        {"max_leverage": float("inf")},
        {"max_leverage": "1"},
    ],
)
def test_unusable_limits_cannot_disable_checks(limits):
    with pytest.raises(ValueError):
        RiskLimitsConfig.model_validate(limits)
    with pytest.raises(ValueError):
        RiskLimits(**limits)


def test_supported_zero_budget_and_disabled_limits_still_round_trip():
    limits = RiskLimits(max_position_size_quote=0, max_open_executors=0, max_leverage=1)
    assert (
        asdict(limits) == RiskLimitsConfig.model_validate(asdict(limits)).model_dump()
    )
    assert limits.max_drawdown_pct == -1


def test_drawdown_schema_explains_the_exposure_basis():
    fields = RiskLimitsConfig.model_json_schema()["properties"]
    for name in ("max_drawdown_pct", "shutdown_drawdown_pct"):
        assert "quote exposure" in fields[name]["description"]
        assert "not account-equity" in fields[name]["description"]


@pytest.mark.parametrize("field", ["max_drawdown_pct", "shutdown_drawdown_pct"])
def test_live_single_tick_cannot_claim_a_drawdown_guard_without_a_journal(field):
    config = {"execution_mode": "run_once", "risk_limits": {field: 0}}
    with pytest.raises(ValueError, match="run_once cannot enforce drawdown"):
        AgentConfig.from_dict(config)
    config["execution_mode"] = "dry_run"
    assert AgentConfig.from_dict(config).risk_limits.model_dump()[field] == 0


@pytest.mark.parametrize("metric", [float("nan"), float("inf"), -1, True, None])
def test_invalid_drawdown_data_keeps_the_risk_gate_blocked(metric):
    state = RiskEngine(RiskLimits(max_drawdown_pct=5)).get_state(
        SimpleNamespace(get_drawdown_pct=lambda: metric)
    )
    assert state.is_blocked
    assert "risk state unavailable" in state.block_reason


@pytest.mark.parametrize("saved", ["risk_limits: [", "[]", "null", ""])
def test_unreadable_saved_config_never_falls_back_to_trading_defaults(tmp_path, saved):
    (tmp_path / "config.yml").write_text(saved)
    with pytest.raises(ValueError):
        load_full_config(tmp_path, {"risk_limits": {"max_position_size_quote": 500}})


def test_corrupt_config_errors_do_not_echo_yaml_source(tmp_path):
    (tmp_path / "config.yml").write_text("risk_limits: [private-value")
    with pytest.raises(ValueError) as error:
        load_full_config(tmp_path)
    assert "private-value" not in str(error.value)


def test_invalid_save_leaves_the_previous_config_intact(tmp_path):
    save_full_config(tmp_path, {"risk_limits": {"max_position_size_quote": 8}})
    before = (tmp_path / "config.yml").read_bytes()
    with pytest.raises(ValueError, match="max_consecutive_losses"):
        save_full_config(tmp_path, {"risk_limits": {"max_consecutive_losses": 3}})
    assert (tmp_path / "config.yml").read_bytes() == before


def test_request_override_can_replace_an_unsupported_stored_risk_config(tmp_path):
    (tmp_path / "config.yml").write_text("risk_limits:\n  daily_loss_pct: 2\n")
    config = load_full_config(
        tmp_path, overrides={"risk_limits": {"max_position_size_quote": 8}}
    )
    assert config["risk_limits"] == {"max_position_size_quote": 8}


def test_engine_rejects_invalid_limits_before_allocating_a_session(tmp_path):
    from condor.agents.engine import TickEngine

    with pytest.raises(ValueError, match="daily_loss_pct"):
        TickEngine(
            agent=SimpleNamespace(slug="trader"),
            strategy=SimpleNamespace(home=tmp_path, slug="sol"),
            config={"risk_limits": {"daily_loss_pct": 2}},
            chat_id=0,
            user_id=555,
        )
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def loop_api(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    cm = SimpleNamespace(
        is_admin=lambda *_: False,
        has_server_access=lambda *_a, **_k: True,
    )
    monkeypatch.setattr("config_manager.get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
    agent = AgentStore().create(name="Risk Test Agent")
    store = StrategyStore()
    strategy = store.create(agent.slug, "SOL Test", instructions="Observe only")
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=555, username="test", first_name="Test", role="user"
    )
    return TestClient(app), agent, strategy, store


def test_web_create_refuses_unknown_risk_limits_without_a_partial_loop(loop_api):
    client, agent, _strategy, store = loop_api
    response = client.post(
        f"/agents/{agent.slug}/strategies",
        json={"name": "Bad Risk", "config": {"risk_limits": {"daily_loss_pct": 2}}},
    )
    assert response.status_code == 400
    assert "daily_loss_pct" in response.json()["detail"]
    assert store.get(agent.slug, "bad_risk") is None


def test_web_update_refuses_unknown_limits_and_keeps_the_previous_file(loop_api):
    client, agent, strategy, _store = loop_api
    save_full_config(strategy.home, {"risk_limits": {"max_position_size_quote": 8}})
    before = (strategy.home / "config.yml").read_bytes()
    response = client.put(
        f"/agents/{agent.slug}/strategies/{strategy.slug}/config",
        json={"config": {"risk_limits": {"daily_loss_pct": 2}}},
    )
    assert response.status_code == 400
    assert (strategy.home / "config.yml").read_bytes() == before


def test_web_markdown_edit_validates_risk_limits_before_replacing_the_loop(loop_api):
    client, agent, strategy, _store = loop_api
    before = strategy.source.read_bytes()
    response = client.put(
        f"/agents/{agent.slug}/strategies/{strategy.slug}",
        json={
            "content": "---\nname: SOL Test\ndefault_config:\n"
            "  risk_limits:\n    daily_loss_pct: 2\n---\nObserve only\n"
        },
    )
    assert response.status_code == 400
    assert strategy.source.read_bytes() == before


@pytest.mark.parametrize(
    "config",
    [
        {"risk_limits": {"daily_loss_pct": 2}},
        {"execution_mode": "run_once", "risk_limits": {"max_drawdown_pct": 5}},
    ],
)
def test_web_start_refuses_invalid_overrides_before_constructing_an_engine(
    loop_api, monkeypatch, config
):
    client, agent, strategy, _store = loop_api

    def forbidden_engine(*_args, **_kwargs):
        pytest.fail("invalid configuration reached the runtime")

    monkeypatch.setattr("condor.agents.engine.TickEngine", forbidden_engine)
    response = client.post(
        f"/agents/{agent.slug}/strategies/{strategy.slug}/start",
        json={"config": config},
    )
    assert response.status_code == 400
    assert not (strategy.home / "sessions").exists()


def test_web_start_refuses_corrupt_saved_config_before_constructing_an_engine(
    loop_api, monkeypatch
):
    client, agent, strategy, _store = loop_api
    (strategy.home / "config.yml").write_text("risk_limits: [")

    def forbidden_engine(*_args, **_kwargs):
        pytest.fail("corrupt saved configuration reached the runtime")

    monkeypatch.setattr("condor.agents.engine.TickEngine", forbidden_engine)
    response = client.post(
        f"/agents/{agent.slug}/strategies/{strategy.slug}/start", json={}
    )
    assert response.status_code == 400
    assert "Cannot read saved loop config" in response.json()["detail"]
    assert not (strategy.home / "sessions").exists()


def test_mcp_update_returns_a_refusal_without_replacing_valid_defaults(loop_api):
    from mcp_servers.condor.tools.trading_agent import manage_loops

    _client, _agent, strategy, store = loop_api
    response = manage_loops(
        "update", loop_id=strategy.key, config={"risk_limits": {"daily_loss_pct": 2}}
    )
    assert "daily_loss_pct" in response["error"]
    assert store.get_by_key(strategy.key).default_config == {}
