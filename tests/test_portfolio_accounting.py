"""Account-value regressions: transfers conserve money and PnL is not a balance."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from condor.fetchers.portfolio import PORTFOLIO_HISTORY_RANGES, fetch_portfolio_history
from condor.web.models import WebUser
from condor.web.routes import portfolio

USER = WebUser(id=1, role="admin")


def token(name, value, **extra):
    return {
        "token": name,
        "units": value,
        "available_units": value,
        "value": value,
        **extra,
    }


def bind(monkeypatch, payload):
    sds = SimpleNamespace(get_or_fetch=AsyncMock(return_value=payload))
    monkeypatch.setattr(portfolio, "get_server_data_service", lambda: sds)
    monkeypatch.setattr(portfolio, "get_config_manager", lambda: None)


async def history(monkeypatch, states, keyed=False):
    snapshots = (
        {str(1000 + i * 300): state for i, state in enumerate(states)}
        if keyed
        else [
            {"timestamp": 1000 + i * 300, "state": state}
            for i, state in enumerate(states)
        ]
    )
    original = deepcopy(snapshots)
    bind(monkeypatch, snapshots)
    result = await portfolio.get_portfolio_history(
        "test", range="1D", breakdown=True, user=USER
    )
    assert snapshots == original, "Parsing must not mutate the shared cache"
    for point in result.points:
        assert sum(point.tokens.values()) == pytest.approx(point.total_usd)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("keyed", [False, True])
async def test_full_transfers_and_final_withdrawal_do_not_resurrect_balances(
    monkeypatch, keyed
):
    result = await history(
        monkeypatch,
        [
            {"master": {"binance": [token("USDT", 1000)]}},
            {"master": {"binance_perpetual": [token("USDT", 1000)]}},
            {"master": {"binance": [token("USDT", 1000)]}},
            {"master": {"binance": [], "binance_perpetual": []}},
        ],
        keyed,
    )
    assert [p.total_usd for p in result.points] == [1000, 1000, 1000, 0]
    assert result.valuation == "wallet"


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_zero", [False, True])
async def test_sold_token_is_removed_from_stack(monkeypatch, explicit_zero):
    after = [token("USDT", 1000)]
    if explicit_zero:
        after.append(token("BTC", 0))
    result = await history(
        monkeypatch,
        [
            {"master": {"binance": [token("BTC", 1000)]}},
            {"master": {"binance": after}},
        ],
    )
    assert result.points[-1].tokens == {"USDT": 1000}


@pytest.mark.asyncio
async def test_equity_includes_float_loss_without_changing_spendable_units(monkeypatch):
    bind(
        monkeypatch,
        {
            "master": {
                "binance": [token("USDT", 100)],
                "binance_perpetual": [
                    token("USDT", 1000, available_units=600, equity_value=800)
                ],
            }
        },
    )
    result = await portfolio.get_portfolio("test", refresh=False, user=USER)
    assert result.total_usd == 1100
    assert result.equity_usd == 900
    assert result.unrealized_pnl_usd == -200
    futures = result.connectors[1]
    assert futures.account_name == "master"
    assert futures.equity_usd == 800
    assert futures.balances[0].total == 1000
    assert futures.balances[0].available == 600
    assert futures.balances[0].usd_value == 1000


@pytest.mark.asyncio
async def test_missing_perpetual_equity_is_unknown_not_zero(monkeypatch):
    bind(monkeypatch, {"master": {"binance_perpetual": [token("USDT", 1000)]}})
    result = await portfolio.get_portfolio("test", refresh=False, user=USER)
    assert result.total_usd == 1000
    assert result.equity_usd is None
    assert result.unrealized_pnl_usd is None


@pytest.mark.asyncio
async def test_new_history_tracks_floating_losses(monkeypatch):
    result = await history(
        monkeypatch,
        [
            {"master": {"binance_perpetual": [token("USDT", 1000, equity_value=800)]}},
            {"master": {"binance_perpetual": [token("USDT", 1000, equity_value=750)]}},
        ],
    )
    assert result.valuation == "equity"
    assert [p.total_usd for p in result.points] == [800, 750]


@pytest.mark.asyncio
async def test_legacy_history_does_not_mix_wallet_and_equity(monkeypatch):
    result = await history(
        monkeypatch,
        [
            {"master": {"binance_perpetual": [token("USDT", 1000)]}},
            {"master": {"binance_perpetual": [token("USDT", 1000, equity_value=750)]}},
        ],
    )
    assert result.valuation == "wallet"
    assert [p.total_usd for p in result.points] == [1000, 1000]


@pytest.mark.asyncio
async def test_history_and_current_use_the_same_unified_account_deduplication(
    monkeypatch,
):
    state = {
        "master": {
            "hyperliquid": [token("USDC", 1000)],
            "hyperliquid_perpetual": [token("USD", 1000)],
        }
    }
    result = await history(monkeypatch, [state])
    assert result.points[0].total_usd == 1000


@pytest.mark.asyncio
async def test_negative_balances_are_kept_in_totals_and_breakdown(monkeypatch):
    state = {"master": {"binance": [token("USDT", 100), token("USDC", -20)]}}
    result = await history(monkeypatch, [state])
    assert result.points[0].total_usd == 80
    assert result.points[0].tokens["USDC"] == -20
    bind(monkeypatch, state)
    current = await portfolio.get_portfolio("test", refresh=False, user=USER)
    assert len(current.connectors[0].balances) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("range_key", PORTFOLIO_HISTORY_RANGES)
async def test_history_range_is_sent_in_milliseconds(monkeypatch, range_key):
    now = 1_791_350_000
    monkeypatch.setattr("condor.fetchers.portfolio.time.time", lambda: now)
    client = SimpleNamespace(
        portfolio=SimpleNamespace(get_history=AsyncMock(return_value=[]))
    )
    await fetch_portfolio_history(client, range_key)
    lookback, interval = PORTFOLIO_HISTORY_RANGES[range_key]
    client.portfolio.get_history.assert_awaited_once_with(
        start_time=(now - lookback) * 1000,
        interval=interval,
        limit=500,
    )
