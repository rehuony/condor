"""The DEX callback router is a prefix table; parsing matches the old if-chain."""

import asyncio
from types import SimpleNamespace

import pytest

import handlers.dex.router as router
from handlers._routing import dispatch_action


def _route(prefix):
    return next(row for row in router.PREFIX_ACTIONS if row[0] == prefix)


def test_longer_prefixes_are_matched_before_their_shorter_siblings():
    prefixes = [prefix for prefix, _handler, _parse in router.PREFIX_ACTIONS]
    assert len(prefixes) == len(set(prefixes))
    for i, longer in enumerate(prefixes):
        for shorter in prefixes[:i]:
            assert not longer.startswith(shorter), (shorter, longer)


def test_page_and_noop_actions_moved_to_the_simple_table():
    for action in (
        "swap_hist_page_prev",
        "swap_hist_page_next",
        "lp_hist_page_prev",
        "lp_hist_page_next",
        "add_position_from_pool",
        "noop",
        "lpm_noop",
    ):
        assert action in router.SIMPLE_ACTIONS
    assert router.SIMPLE_ACTIONS["swap_hist_page_prev"].keywords == {
        "direction": "prev"
    }
    assert router.SIMPLE_ACTIONS["lp_hist_page_next"].keywords == {"direction": "next"}


def test_noop_buttons_are_awaitable():
    update, context = SimpleNamespace(), SimpleNamespace(user_data={})
    assert (
        asyncio.run(dispatch_action(update, context, "noop", router.SIMPLE_ACTIONS, ()))
        is True
    )


@pytest.mark.parametrize(
    "action", ["swap_quote", "swap_execute", "swap_search", "explore_pools"]
)
def test_retired_callbacks_are_not_dispatched(action):
    assert (
        asyncio.run(
            dispatch_action(
                None, None, action, router.SIMPLE_ACTIONS, router.PREFIX_ACTIONS
            )
        )
        is False
    )


@pytest.mark.parametrize(
    "prefix, rest, expected",
    [
        ("pos_view_tf:", "3:4h", ("3", "4h")),
        ("pos_view_tf:", "3", ("3", "1h")),
        ("pool_ohlcv:", "1h:usd", ("1h", "usd")),
        ("pool_ohlcv:", "1h", ("1h", "usd")),
        ("pool_combined:", "15m:quote", ("15m", "quote")),
        ("lpm_nav:", "inst:2", ("inst", 2)),
        ("lpm_nav:", "inst", None),
        ("lpm_detail:", "inst:0", ("inst", 0)),
        ("lpm_oor:", "inst:7", ("inst", 7)),
        ("lpm_cancel_countdown:", "inst:pos-9", ("inst", "pos-9")),
        ("gecko_trending_", "all", None),
        ("gecko_trending_", "solana", "solana"),
        ("gecko_new_", "all", None),
        ("gecko_pool:", "12", 12),
        ("lp_pos_view:", "0", 0),
    ],
)
def test_multi_part_parsers_match_the_old_split_logic(prefix, rest, expected):
    _prefix, _handler, parse = _route(prefix)
    assert parse(rest) == expected


def test_missing_parts_are_consumed_silently(monkeypatch):
    calls = []

    async def fake_nav(update, context, instance_id, index):
        calls.append((instance_id, index))

    monkeypatch.setattr(router, "handle_lpm_navigation", fake_nav)
    monkeypatch.setattr(
        router,
        "PREFIX_ACTIONS",
        (("lpm_nav:", router._pair(fake_nav), router._instance_and_int),),
    )
    update, context = SimpleNamespace(), SimpleNamespace(user_data={})

    def run(action):
        return asyncio.run(
            dispatch_action(update, context, action, {}, router.PREFIX_ACTIONS)
        )

    assert run("lpm_nav:inst") is True
    assert run("lpm_nav:inst:4") is True
    assert calls == [("inst", 4)]


def test_history_filters_bind_their_filter_name():
    calls = []

    async def fake_filter(update, context, filter_type, value):
        calls.append((filter_type, value))

    handler = router._filter(fake_filter, "connector")
    asyncio.run(handler(None, None, "jupiter"))
    assert calls == [("connector", "jupiter")]


def test_dex_menus_never_capture_text():
    for menu_state in ("swap", "add_position", "pool_list_results", "liquidity"):
        assert menu_state not in router.MESSAGE_STATE_HANDLERS
    assert router.MESSAGE_STATE_HANDLERS["swap_quick"] is router.process_swap
    assert router.MESSAGE_STATE_HANDLERS["add_position_quick"] is (
        router.process_add_position
    )


def test_add_position_text_never_opens_a_position():
    """A positional line used to open a position on-chain; now it is refused."""
    from unittest.mock import AsyncMock

    import handlers.dex.pools.add_position.input as add_input

    answer = AsyncMock()
    user_data = {"dex_state": "add_position_quick", "add_position_params": {}}
    update = SimpleNamespace(
        message=SimpleNamespace(answer=answer, delete=AsyncMock()),
        effective_chat=SimpleNamespace(id=1),
    )
    context = SimpleNamespace(user_data=user_data, bot=SimpleNamespace())

    asyncio.run(
        add_input.process_add_position(update, context, "PoolAddr 0.8 1.2 10 20")
    )

    assert not hasattr(add_input, "get_client")
    assert user_data["add_position_params"] == {}
    assert user_data["dex_state"] == "add_position_quick"
    assert "/cancel" in answer.await_args.args[0]
