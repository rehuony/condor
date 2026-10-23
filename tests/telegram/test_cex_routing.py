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
    # The menu state "trade" is navigation; only button-armed prompts read text.
    assert set(cex.TEXT_INPUT_HANDLERS) == {
        "trade_quick",
        "trade_set_pair",
        "trade_set_amount",
        "trade_set_price",
        "trade_set_leverage",
    }


def test_an_older_trade_menu_cannot_execute(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import handlers.cex.trade.execute as execute
    from utils import telegram_ui as ui

    get_client = AsyncMock()
    monkeypatch.setattr(execute, "get_client", get_client)
    message = SimpleNamespace(message_id=1, edit_text=AsyncMock())
    update = SimpleNamespace(
        callback_query=SimpleNamespace(message=message),
        effective_chat=SimpleNamespace(id=7),
    )
    user_data = {"trade_params": {"connector": "binance", "amount": "1"}}
    ui.remember_menu(user_data, "cex_trade", 2)

    asyncio.run(
        execute.handle_trade_execute(update, SimpleNamespace(user_data=user_data))
    )

    get_client.assert_not_called()
    assert message.edit_text.await_args.args[0] == ui.STALE_MENU
