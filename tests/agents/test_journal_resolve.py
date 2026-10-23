"""An unknown session number resolves to no journal, never a phantom one (CORR-654).

The MCP ``journal_read``/``journal_write`` tools fall back to
``resolve_agent_dirs`` for a model-supplied ``agent_id`` with no registered
engine. A mistyped or stale session number used to resolve to the
not-yet-existing ``sessions/session_N``, which ``JournalManager`` then created
-- a phantom session listed in the Runs rail and skipped by
``next_session_number``.
"""

import pytest

from condor.agents.journal import resolve_agent_dirs
from mcp_servers.condor.tools import trading_agent


@pytest.fixture
def base(tmp_path, monkeypatch):
    root = tmp_path / "agents"
    import condor.agents.engine as engine_module
    import condor.agents.journal as journal_module

    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(root))
    monkeypatch.setattr(journal_module, "local_agents_root", lambda: root)
    monkeypatch.setattr(engine_module, "get_engine", lambda _aid: None)
    strategy_dir = root / "brigado" / "loops" / "grid"
    strategy_dir.mkdir(parents=True)
    return strategy_dir


def test_an_unknown_session_resolves_to_nothing_and_creates_nothing(base):
    assert resolve_agent_dirs("brigado.grid_42") == (None, None)
    assert not (base / "sessions" / "session_42").exists()


def test_the_mcp_tools_refuse_an_unknown_session_without_writing(base):
    assert trading_agent.journal_write(
        agent_id="brigado.grid_42", entry_type="action", text="x", tick=1
    ) == {"error": "no journal available for this agent"}
    assert trading_agent.journal_read(agent_id="brigado.grid_42") == {
        "content": "(no journal available for this agent)"
    }
    assert not (base / "sessions").exists()
    assert not (base / "trading_sessions").exists()


def test_an_existing_session_still_resolves_and_is_written(base):
    session = base / "sessions" / "session_1"
    session.mkdir(parents=True)
    assert resolve_agent_dirs("brigado.grid_1") == (session, base)
    assert trading_agent.journal_write(
        agent_id="brigado.grid_1", entry_type="action", text="x", tick=1
    ) == {"written": True}
    assert sorted(p.name for p in (base / "sessions").iterdir()) == ["session_1"]


def test_an_experiment_id_still_resolves_to_its_strategy_dir(base):
    assert resolve_agent_dirs("brigado.grid_e3") == (None, base)
    result = trading_agent.journal_write(
        agent_id="brigado.grid_e3", entry_type="action", text="x", tick=1
    )
    assert "skipped" in result
    assert not (base / "sessions").exists()


def test_current_journal_tool_sections_keep_their_contract(base):
    from condor.agents.journal import JournalManager

    session = base / "sessions" / "session_1"
    journal = JournalManager("brigado.grid_1", session_dir=session, agent_dir=base)
    assert trading_agent.journal_write(
        agent_id="brigado.grid_1", entry_type="state", text="Holding SOL"
    ) == {"written": True}
    for section in ("state", "summary"):
        assert trading_agent.journal_read("brigado.grid_1", section=section) == {
            "content": "Holding SOL"
        }
    journal.append_action(1, "Hold", "Waiting for the signal")
    assert "Hold" in trading_agent.journal_read("brigado.grid_1")["content"]
    assert journal.entry_count() == 0
    snapshot = journal.save_full_snapshot(
        tick=1,
        timestamp="2026-10-21 10:00:00",
        system_prompt="Observe",
        response_text="Hold",
        tool_calls=[],
        executors_data="No executors",
        risk_state={},
        duration=1.0,
    )
    assert trading_agent.journal_read("brigado.grid_1", section="run:1") == {
        "content": snapshot.read_text()
    }
    assert journal.entry_count() == 1
    assert (
        trading_agent.journal_read("brigado.grid_1", section="runs")["runs"][0]["tick"]
        == 1
    )
