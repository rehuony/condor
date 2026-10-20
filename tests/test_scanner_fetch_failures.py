"""Ticker failures must reach the routine store as failures, not success text."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

from condor.routine_store import RoutineStore
from routines import market_scanner, mean_reversion_scanner


@pytest.fixture(
    params=[mean_reversion_scanner, market_scanner], ids=["mean-reversion", "market"]
)
def scanner(request, monkeypatch):
    module = request.param
    if module is market_scanner:
        monkeypatch.setattr(module, "get_client", AsyncMock(return_value=object()))
    return module


@pytest.mark.parametrize(
    "error, detail",
    [
        (TimeoutError(), "TimeoutError): request timed out"),
        (
            aiohttp.ClientConnectionError("connection reset"),
            "ClientConnectionError): connection reset",
        ),
    ],
)
def test_ticker_fetch_raises_with_context_and_original_cause(
    scanner, monkeypatch, error, detail
):
    monkeypatch.setattr(scanner, "fetch_top_pairs", AsyncMock(side_effect=error))

    with pytest.raises(RuntimeError, match="Failed to fetch Binance tickers") as raised:
        asyncio.run(
            scanner.run(
                scanner.Config(), SimpleNamespace(chat_id=0, user_data={}, bot=None)
            )
        )

    assert detail in str(raised.value)
    assert raised.value.__cause__ is error


@pytest.mark.parametrize("failed", [True, False], ids=["timeout", "successful-empty"])
def test_scanner_outcome_reaches_instance_hooks(scanner, monkeypatch, failed):
    fetch = (
        AsyncMock(side_effect=TimeoutError()) if failed else AsyncMock(return_value=[])
    )
    monkeypatch.setattr(scanner, "fetch_top_pairs", fetch)
    store = RoutineStore()
    name = scanner.__name__.split(".")[-1]
    store._instances["i1"] = store._new_instance_meta(name, {}, "test", 0, source="web")
    routine = SimpleNamespace(
        name=name, source="global", config_class=scanner.Config, run_fn=scanner.run
    )
    hooks = AsyncMock()
    notification = AsyncMock()
    monkeypatch.setattr(store, "_fire_hooks", hooks)
    monkeypatch.setattr(store, "_report_run", notification)

    asyncio.run(store._run_oneshot("i1", routine, {}, "test"))

    instance = store._instances["i1"]
    assert instance["status"] == ("failed" if failed else "completed")
    assert instance["report_id"] is None
    assert instance["run_count"] == 1
    assert hooks.await_args.args[-1] is failed
    assert notification.await_args.args[-1] == instance["error"]
    if failed:
        assert "TimeoutError): request timed out" in instance["error"]
        assert "Failed to fetch Binance tickers" in store.get_result("i1").text
    else:
        assert instance["error"] is None
        assert instance["last_result"].startswith("No pairs found matching")
