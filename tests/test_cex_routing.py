"""The CEX callback router is table-driven: simple actions, prefixed actions, text states."""

import asyncio
from types import SimpleNamespace

import handlers.cex as cex
from handlers._routing import dispatch_action, noop


def _record(calls, name):
    async def handler(update, context, *args, **kwargs):
        calls.append((name, args, kwargs))

    return handler


def test_simple_and_prefixed_actions_reach_their_handlers():
    calls = []
    simple = {"trade_refresh": _record(calls, "refresh"), "noop": noop}
    prefixes = (
        ("cancel_order:", _record(calls, "cancel"), int),
        ("trade_connector_", _record(calls, "connector"), str),
    )
    update, context = SimpleNamespace(), SimpleNamespace(user_data={})

    def run(action):
        return asyncio.run(dispatch_action(update, context, action, simple, prefixes))

    assert run("trade_refresh") is True
    assert run("noop") is True
    assert run("cancel_order:3") is True
    assert run("trade_connector_binance_perpetual") is True
    assert run("nope") is False

    assert calls == [
        ("refresh", (), {}),
        ("cancel", (3,), {}),
        ("connector", ("binance_perpetual",), {}),
    ]


def test_cex_tables_only_hold_prefixed_rows_once():
    prefixes = [prefix for prefix, _handler, _parse in cex.PREFIX_ACTIONS]
    assert len(prefixes) == len(set(prefixes))
    assert not set(prefixes) & set(cex.SIMPLE_ACTIONS)


def test_order_search_actions_carry_their_status():
    assert cex.SIMPLE_ACTIONS["search_orders"].keywords == {"status": "ALL"}
    assert cex.SIMPLE_ACTIONS["search_filled"].keywords == {"status": "FILLED"}
    assert cex.SIMPLE_ACTIONS["search_cancelled"].keywords == {"status": "CANCELLED"}


def test_every_text_state_has_a_processor():
    assert set(cex.TEXT_INPUT_HANDLERS) == {
        "trade",
        "trade_set_pair",
        "trade_set_amount",
        "trade_set_price",
        "trade_set_leverage",
    }
    assert cex._COMPLETING_STATES == {"trade"}
