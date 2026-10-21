"""agent_id → disk path: one parse, one strategy-dir composition (ARCH-676).

``enumerate_agent_ids`` writes the ids; ``parse_agent_id`` is their inverse,
and ``resolve_agent_dirs`` / the MCP ``_resolve_experiment_file`` build paths
from it with the same composition as ``Strategy.home`` and the current
``sessions/`` and ``dry_runs/`` layouts.
"""

import pytest

from condor.agents import sessions_index
from condor.agents.journal import (
    JournalManager,
    next_experiment_number,
    resolve_agent_dirs,
)
from condor.agents.sessions_index import parse_agent_id, strategy_dir_for_run_key
from mcp_servers.condor.tools.trading_agent import _resolve_experiment_file


@pytest.fixture
def agents_root(tmp_path, monkeypatch):
    root = tmp_path / "agents"
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(root))
    return root


def _touch(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_parse_agent_id_inverts_every_id_enumerate_agent_ids_emits(tmp_path):
    strategy_dir = tmp_path / "s"
    _touch(strategy_dir / "sessions" / "session_1" / "journal.md")
    _touch(strategy_dir / "sessions" / "session_2" / "journal.md")
    _touch(strategy_dir / "dry_runs" / "experiment_3.md")
    _touch(strategy_dir / "dry_runs" / "experiment_12.md")
    _touch(strategy_dir / "trading_sessions" / "session_99" / "journal.md")
    _touch(strategy_dir / "experiments" / "experiment_99.md")

    ids = sessions_index.enumerate_agent_ids("a.s", strategy_dir)
    assert {kind for _, _, kind in ids} == {"session", "experiment"}
    assert len(ids) == 4
    assert sessions_index.count_experiments(strategy_dir) == 2
    assert next_experiment_number(strategy_dir) == 13
    assert sessions_index.find_session_dir(strategy_dir, 99) is None
    assert sessions_index.find_experiment_file(strategy_dir, 99) is None
    for agent_id, n, kind in ids:
        assert parse_agent_id(agent_id) == ("a.s", n, kind)


@pytest.mark.parametrize("bad", ["a.s", "a.s_x", "a.s_e", "a.s_", "_1", "a.s_1x"])
def test_parse_agent_id_rejects_malformed_ids(bad):
    assert parse_agent_id(bad) is None


def test_strategy_dir_is_the_strategy_home_composition(agents_root):
    assert strategy_dir_for_run_key("a.s") == agents_root / "a" / "loops" / "s"
    # The dot-less legacy flat prefix has no producer since FEAT-004.
    assert strategy_dir_for_run_key("flat") is None


def test_a_current_session_dir_is_resolved_without_creating_another(agents_root):
    strategy_dir = agents_root / "a" / "loops" / "s"
    session = strategy_dir / "sessions" / "session_1"
    _touch(session / "journal.md", "# Journal - a.s_1\n")

    assert resolve_agent_dirs("a.s_1") == (session, strategy_dir)
    # The MCP call shape reuses the current journal.
    JournalManager("a.s_1", *resolve_agent_dirs("a.s_1"))
    assert sorted(p.name for p in (strategy_dir / "sessions").iterdir()) == [
        "session_1"
    ]


def test_a_session_not_on_disk_resolves_to_nothing(agents_root):
    # CORR-654: no phantom session dir for an unknown session number.
    strategy_dir = agents_root / "a" / "loops" / "s"
    strategy_dir.mkdir(parents=True)
    assert resolve_agent_dirs("a.s_4") == (None, None)
    assert resolve_agent_dirs("a.s_e4") == (None, strategy_dir)
    (strategy_dir / "sessions" / "session_4").mkdir(parents=True)
    assert resolve_agent_dirs("a.s_4") == (
        strategy_dir / "sessions" / "session_4",
        strategy_dir,
    )


def test_unknown_or_malformed_ids_resolve_to_nothing(agents_root):
    (agents_root / "a" / "loops" / "s").mkdir(parents=True)
    (agents_root / "flat").mkdir(parents=True)
    assert resolve_agent_dirs("a.missing_1") == (None, None)
    assert resolve_agent_dirs("a.s_x") == (None, None)
    assert resolve_agent_dirs("flat_1") == (None, None)


def test_resolve_experiment_file_finds_the_current_dir(agents_root):
    strategy_dir = agents_root / "a" / "loops" / "s"
    _touch(strategy_dir / "dry_runs" / "experiment_2.md")

    assert _resolve_experiment_file("a.s_e2") == (
        strategy_dir / "dry_runs" / "experiment_2.md",
        2,
    )
    assert _resolve_experiment_file("a.s_e3") == (None, 3)
    assert _resolve_experiment_file("a.missing_e3") == (None, 3)
    assert _resolve_experiment_file("a.s_2") == (None, None)
