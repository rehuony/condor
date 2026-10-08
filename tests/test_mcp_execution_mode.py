"""Constrained MCP calls are refused at dispatch, without ACP approval RPCs.

Every sink is a mock. No routine, snippet, venue request or model is executed.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import FastMCP

from condor.agents.risk import RiskEngine, RiskState, auto_approve_with_risk_check
from condor.runtime.toolsets import _condor_mcp_args, _hummingbot_mcp_args
from mcp_servers._profiles import parse_execution_mode
from mcp_servers.condor import server as condor_server
from mcp_servers.hummingbot_api import server as hb_server


def _server(module, mode="dry_run"):
    server = FastMCP("isolated-mode-test")
    module.register_tools(server, "tick", execution_mode=mode)
    return server


@pytest.mark.parametrize(
    "action", ["run", "run_async", "start", "create_routine", "edit_routine", "stop"]
)
def test_dry_run_dispatch_refuses_routine_execution_without_permission_rpc(
    monkeypatch, action
):
    sink = AsyncMock(return_value={"mock": True})
    monkeypatch.setattr(condor_server.routines, "manage_routines", sink)
    server = _server(condor_server)
    with pytest.raises(Exception, match="Dry-run refused"):
        asyncio.run(
            server.call_tool("manage_routines", {"action": action, "name": "mock"})
        )
    sink.assert_not_awaited()


@pytest.mark.parametrize("action", ["list", "describe", "read_routine", "get_instance"])
def test_dry_run_keeps_routine_discovery(monkeypatch, action):
    sink = AsyncMock(return_value={"mock": True})
    monkeypatch.setattr(condor_server.routines, "manage_routines", sink)
    asyncio.run(
        _server(condor_server).call_tool(
            "manage_routines", {"action": action, "name": "mock"}
        )
    )
    sink.assert_awaited_once()


@pytest.mark.parametrize("mode", ["", "loop", "run_once"])
def test_non_dry_run_routine_dispatch_is_unchanged(monkeypatch, mode):
    sink = AsyncMock(return_value={"mock": True})
    monkeypatch.setattr(condor_server.routines, "manage_routines", sink)
    asyncio.run(
        _server(condor_server, mode).call_tool(
            "manage_routines", {"action": "run", "name": "mock"}
        )
    )
    sink.assert_awaited_once()


@pytest.mark.parametrize(
    "name,args",
    [
        ("run_code", {"code": "raise AssertionError('must never execute')"}),
        ("delegate", {"action": "ask", "agent": "mock", "task": "mock"}),
        ("delegate", {"action": "start", "agent": "mock", "task": "mock"}),
    ],
)
def test_dry_run_blocks_default_code_action_and_indirect_execution(name, args):
    with pytest.raises(Exception, match="Dry-run refused"):
        asyncio.run(_server(condor_server).call_tool(name, args))


def test_dry_run_order_never_acquires_the_api_client(monkeypatch):
    sink = AsyncMock(side_effect=AssertionError("must never reach API client"))
    monkeypatch.setattr(hb_server.hummingbot_client, "get_client", sink)
    with pytest.raises(Exception, match="Dry-run refused"):
        asyncio.run(
            _server(hb_server).call_tool(
                "create_order_executor",
                {
                    "connector_name": "binance_perpetual",
                    "trading_pair": "SOL-USDT",
                    "side": 1,
                    "amount": "0.1",
                    "execution_strategy": "MARKET",
                },
            )
        )
    sink.assert_not_awaited()


@pytest.mark.parametrize(
    "name,args",
    [
        (
            "clear_position_held",
            {"connector_name": "binance", "trading_pair": "SOL-USDT"},
        ),
        ("resolve_orphaned_position", {"executor_id": "mock"}),
    ],
)
def test_dry_run_cannot_hide_position_tracking(monkeypatch, name, args):
    sink = AsyncMock(side_effect=AssertionError("must never reach API client"))
    monkeypatch.setattr(hb_server.hummingbot_client, "get_client", sink)
    with pytest.raises(Exception, match="Dry-run refused"):
        asyncio.run(_server(hb_server).call_tool(name, args))
    sink.assert_not_awaited()


def test_dry_run_market_read_keeps_schema_and_dispatch(monkeypatch):
    client = object()
    monkeypatch.setattr(
        hb_server.hummingbot_client, "get_client", AsyncMock(return_value=client)
    )
    sink = AsyncMock(return_value={"candles": []})
    monkeypatch.setattr(hb_server.market_data_tools, "get_market_data", sink)
    asyncio.run(
        _server(hb_server).call_tool(
            "get_market_data", {"action": "candles", "trading_pair": "SOL-USDT"}
        )
    )
    assert sink.await_args.kwargs["client"] is client


@pytest.mark.parametrize("module", [condor_server, hb_server])
def test_unknown_mode_never_widens_the_toolset(module):
    with pytest.raises(ValueError, match="Unknown execution mode"):
        _server(module, "dryrun")


def test_the_spawner_passes_mode_to_both_servers(monkeypatch):
    for args in (
        _condor_mcp_args(1, 1, profile="tick", execution_mode="dry_run"),
        _hummingbot_mcp_args(
            {"host": "test", "port": 1}, "test", "tick", execution_mode="dry_run"
        ),
    ):
        monkeypatch.setattr("sys.argv", ["mcp", *args])
        assert parse_execution_mode() == "dry_run"
    monkeypatch.setattr("sys.argv", ["mcp", "--execution-mode", "dryrun"])
    with pytest.raises(SystemExit):
        parse_execution_mode()


@pytest.mark.parametrize("mode", ["dry_run", "loop", "run_once", "shutdown"])
@pytest.mark.parametrize("enforced", [False, True])
def test_gated_client_propagates_mode_and_requires_real_mediation(
    monkeypatch, mode, enforced
):
    from condor.agents.engine import build_gated_client

    mounts = []
    client = SimpleNamespace(enforces_tool_permissions=enforced)

    def mount(*args, **kwargs):
        mounts.append(kwargs)
        return []

    monkeypatch.setattr("condor.runtime.toolsets.build_mcp_servers_for_session", mount)
    monkeypatch.setattr(
        "condor.runtime.llm_client.build_llm_client", lambda *a, **k: client
    )
    engine = SimpleNamespace(
        user_id=1,
        chat_id=1,
        config={},
        agent=SimpleNamespace(slug="mock", tools=[]),
        risk=RiskEngine(),
        ledger=None,
        agent_id="mock",
        _refusals=None,
        _executor_owners=lambda: {},
        _agent_key=lambda: "codex",
    )
    if mode == "dry_run" or enforced:
        assert build_gated_client(engine, RiskState(), None, mode) is client
    else:
        with pytest.raises(RuntimeError, match="before every tool call"):
            build_gated_client(engine, RiskState(), None, mode)
    assert mounts[0]["execution_mode"] == mode


@pytest.mark.parametrize(
    "state", [RiskState(is_blocked=True), RiskState(should_shutdown=True)]
)
@pytest.mark.parametrize("mode", ["loop", "run_once"])
@pytest.mark.parametrize(
    "name,args",
    [
        ("create_grid_executor", {"controller_id": "owned", "total_amount_quote": 1}),
        ("manage_bots", {"action": "deploy", "bot_name": "test"}),
        ("run_code", {"code": "pass"}),
        ("manage_routines", {"action": "run", "name": "mock"}),
        ("delegate", {"action": "ask", "agent": "mock"}),
    ],
)
def test_blocked_risk_state_refuses_new_exposure_including_indirect_calls(
    state, mode, name, args
):
    gate = auto_approve_with_risk_check(RiskEngine(), state, execution_mode=mode)
    result = asyncio.run(
        gate(
            {"tool": name, "input": args}, [{"kind": "allow_once", "optionId": "allow"}]
        )
    )
    assert result["outcome"]["outcome"] == "cancelled"


def test_blocked_risk_state_keeps_owned_stops_and_reads():
    gate = auto_approve_with_risk_check(
        RiskEngine(),
        RiskState(is_blocked=True),
        agent_id="owned",
        executor_owners={"mine": "owned"},
    )
    for name, args, expected in (
        ("stop_executor", {"executor_id": "mine"}, "selected"),
        ("stop_executor", {"executor_id": "theirs"}, "cancelled"),
        ("get_market_data", {"action": "candles"}, "selected"),
    ):
        result = asyncio.run(
            gate(
                {"tool": name, "input": args},
                [{"kind": "allow_once", "optionId": "allow"}],
            )
        )
        assert result["outcome"]["outcome"] == expected
