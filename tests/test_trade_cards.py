"""Card boundaries: publication, user actions and recovery without network I/O."""

import asyncio
import importlib
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from condor.preferences import get_trade_card_margin, set_trade_card_margin
from condor.trade_cards import (
    CardStatus,
    execution,
    get_card,
    list_cards,
    publish,
    render,
    service,
    store,
    tracker,
)
from condor.trade_cards.models import Card
from condor.trade_cards.sizing import margin_amount
from tests.support.trade_cards import group, plan, quote


@pytest.fixture
def setup(monkeypatch):
    from condor.trade_cards import chart

    monkeypatch.setattr(store, "_store", None)
    monkeypatch.setattr(chart, "render_chart", lambda *_: b"png")
    monkeypatch.setattr(
        execution,
        "authorize_target",
        Mock(
            return_value=SimpleNamespace(
                get_server=lambda _: {"host": "fixed.test", "port": 8000},
            )
        ),
    )
    monkeypatch.setattr(execution, "preview", AsyncMock(return_value=quote()))
    monkeypatch.setattr(
        execution, "current_price", AsyncMock(return_value=Decimal("100"))
    )
    monkeypatch.setattr(
        execution, "open_group", AsyncMock(side_effect=lambda card: group(card))
    )
    monkeypatch.setattr(
        execution, "read_group", AsyncMock(side_effect=lambda card: group(card))
    )
    monkeypatch.setattr(
        execution,
        "command",
        AsyncMock(
            side_effect=lambda card: group(
                card, operation={"id": card.operation_id, "status": "COMPLETED"}
            )
        ),
    )
    monkeypatch.delenv("TRADE_CARDS_MODE", raising=False)
    bot = SimpleNamespace(
        send_photo=AsyncMock(return_value=SimpleNamespace(message_id=11)),
        edit_message_caption=AsyncMock(),
        delete_message=AsyncMock(),
    )
    return bot


async def published(bot, **kwargs):
    return await publish(
        plan(),
        [],
        owner_id=7,
        chat_id=7,
        server_name="pinned",
        account_name="account",
        connector_name="binance_perpetual",
        bot=bot,
        **kwargs,
    )


async def action(bot, card, action_name, **kwargs):
    return await service.act(
        card.id, action_name, owner_id=7, chat_id=7, message_id=11, bot=bot, **kwargs
    )


async def reviewed(bot):
    card = await published(bot)
    await action(bot, card, "invest")
    return await action(bot, card, "amount", margin="10")


@pytest.mark.asyncio
async def test_publish_deduplicates_concurrent_signals_and_scopes_target(setup):
    cards = await asyncio.gather(
        *(published(setup, signal_id="pullback:123") for _ in range(2))
    )
    assert sum(card is not None for card in cards) == 1
    setup.send_photo.assert_awaited_once()
    second = await publish(
        plan(),
        [],
        owner_id=8,
        chat_id=8,
        server_name="pinned",
        account_name="account",
        connector_name="binance_perpetual",
        bot=setup,
        signal_id="pullback:123",
    )
    assert second is not None
    assert len(list_cards(owner_id=7)) == len(list_cards(owner_id=8)) == 1


@pytest.mark.asyncio
async def test_mode_is_fixed_on_card_and_invalid_mode_never_sends(setup, monkeypatch):
    monkeypatch.setenv("TRADE_CARDS_MODE", "live")
    card = await published(setup)
    assert card.mode == "live"
    monkeypatch.setenv("TRADE_CARDS_MODE", "dry_run")
    assert get_card(card.id, owner_id=7).mode == "live"
    monkeypatch.setenv("TRADE_CARDS_MODE", "typo")
    with pytest.raises(ValueError):
        await published(setup)
    assert setup.send_photo.await_count == 1


@pytest.mark.asyncio
async def test_ambiguous_send_is_not_replayed(setup):
    setup.send_photo.side_effect = TimeoutError()
    with pytest.raises(RuntimeError, match="未确认"):
        await published(setup, signal_id="123")
    assert await published(setup, signal_id="123") is None
    assert store.get_store().all()[0].delivery_status == "unknown"


@pytest.mark.asyncio
async def test_recipient_and_message_must_match_before_any_execution(setup):
    card = await reviewed(setup)
    for changes in ({"owner_id": 8}, {"chat_id": 8}, {"message_id": 12}):
        args = {"owner_id": 7, "chat_id": 7, "message_id": 11, **changes}
        with pytest.raises(ValueError):
            await service.act(card.id, "confirm", bot=setup, **args)
    execution.open_group.assert_not_awaited()
    assert get_card(card.id, owner_id=8) is None


@pytest.mark.asyncio
async def test_double_tap_places_once_and_pending_cancel_race_keeps_filled_card(setup):
    card = await reviewed(setup)
    execution.open_group.side_effect = lambda card: group(
        card, "PENDING", filled_amount="0", remaining_amount="0"
    )
    answers = await asyncio.gather(
        action(setup, card, "confirm"),
        action(setup, card, "confirm"),
        return_exceptions=True,
    )
    assert sum(isinstance(answer, Card) for answer in answers) == 1
    execution.open_group.assert_awaited_once()
    card = await action(setup, card, "cancel_entry")
    assert card.status == CardStatus.OPEN
    setup.delete_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_replays_frozen_creation_and_restart_recovers_by_same_id(
    setup, monkeypatch
):
    card = await reviewed(setup)
    execution.open_group.side_effect = execution.ExecutionError("unknown")
    card = await action(setup, card, "confirm")
    assert card.status == CardStatus.RECONCILING
    saved_payload = card.create_payload
    execution.read_group.side_effect = execution.ExecutionError("missing", status=404)
    await tracker.tick(setup)
    assert get_card(card.id, owner_id=7).status == CardStatus.RECONCILING
    assert execution.open_group.await_count == 2
    assert all(
        call.args[0].id == card.id for call in execution.open_group.await_args_list
    )
    assert all(
        call.args[0].create_payload == saved_payload
        for call in execution.open_group.await_args_list
    )
    monkeypatch.setattr(store, "_store", None)
    execution.read_group.side_effect = lambda card: group(card, "PENDING")
    await tracker.tick(setup)
    recovered = get_card(card.id, owner_id=7)
    assert recovered.status == CardStatus.PENDING
    assert recovered.create_payload == saved_payload


@pytest.mark.asyncio
async def test_saved_intents_before_send_recover_using_original_identities(
    setup, monkeypatch
):
    card = await reviewed(setup)
    card.create_payload = execution.payload(card)
    frozen = dict(card.create_payload)
    card.transition(CardStatus.PLACING)
    store.get_store().save(card)
    monkeypatch.setattr(store, "_store", None)
    execution.read_group.side_effect = execution.ExecutionError("missing", status=404)
    await tracker.tick(setup)
    opened = get_card(card.id, owner_id=7)
    assert opened.status == CardStatus.OPEN
    execution.open_group.assert_awaited_once()
    assert execution.open_group.await_args.args[0].create_payload == frozen
    opened.operation_id = f"{card.id}-1"
    opened.operation_seq = 1
    opened.pending_action = "reduce_half"
    opened.transition(CardStatus.CLOSING)
    store.get_store().save(opened)
    monkeypatch.setattr(store, "_store", None)
    execution.read_group.side_effect = lambda card: group(card)
    replayed = []

    def command_replay(card):
        replayed.append((card.operation_id, card.pending_action))
        return group(card, operation={"id": card.operation_id, "status": "COMPLETED"})

    execution.command.side_effect = command_replay
    await tracker.tick(setup)
    execution.command.assert_awaited_once()
    assert replayed == [(f"{card.id}-1", "reduce_half")]
    assert get_card(card.id, owner_id=7).status == CardStatus.OPEN


@pytest.mark.asyncio
async def test_command_conflict_recovers_controls_without_replaying_rejected_action(
    setup,
):
    card = await reviewed(setup)
    await action(setup, card, "confirm")
    await action(setup, card, "close")
    execution.command.side_effect = execution.ExecutionError("conflict", status=409)
    result = await action(setup, card, "yes_close")
    assert result.status == CardStatus.RECONCILING
    assert result.pending_action is None
    execution.read_group.side_effect = lambda card: group(
        card, operation={"id": "other", "status": "PENDING"}
    )
    await tracker.tick(setup)
    assert get_card(card.id, owner_id=7).status == CardStatus.RECONCILING
    execution.read_group.side_effect = lambda card: group(
        card, operation={"id": "other", "status": "COMPLETED"}
    )
    await tracker.tick(setup)
    assert get_card(card.id, owner_id=7).status == CardStatus.OPEN
    execution.command.assert_awaited_once()


@pytest.mark.asyncio
async def test_telegram_edit_failure_does_not_prevent_confirmed_order(setup):
    card = await reviewed(setup)
    setup.edit_message_caption.side_effect = [TimeoutError(), None]
    await action(setup, card, "confirm")
    execution.open_group.assert_awaited_once()
    assert get_card(card.id, owner_id=7).status == CardStatus.OPEN


@pytest.mark.asyncio
async def test_close_confirmation_survives_refresh_and_detach_deletes_only_after_ack(
    setup,
):
    card = await reviewed(setup)
    await action(setup, card, "confirm")
    await action(setup, card, "detach")
    await tracker.tick(setup)
    execution.command.assert_not_awaited()
    assert get_card(card.id, owner_id=7).confirm_action == "detach"
    execution.command.side_effect = execution.ExecutionError("unknown")
    card = await action(setup, card, "yes_detach")
    assert card.status == CardStatus.RECONCILING
    operation_id = card.operation_id
    setup.delete_message.assert_not_awaited()
    execution.read_group.side_effect = lambda card: group(
        card, "DETACHED", operation={"id": operation_id, "status": "COMPLETED"}
    )
    await tracker.tick(setup)
    assert get_card(card.id, owner_id=7).status == CardStatus.DETACHED
    setup.delete_message.assert_awaited_once()
    assert execution.command.await_count == 1


@pytest.mark.asyncio
async def test_detach_delete_failure_removes_buttons_and_retries_without_trading(setup):
    card = await reviewed(setup)
    await action(setup, card, "confirm")
    await action(setup, card, "detach")
    execution.command.side_effect = lambda card: group(card, "DETACHED")
    setup.delete_message.side_effect = TimeoutError()
    result = await action(setup, card, "yes_detach")
    assert result.status == CardStatus.DETACHED
    assert not result.message_deleted
    edited = setup.edit_message_caption.await_args.kwargs
    assert edited["reply_markup"] is None
    assert edited["parse_mode"] == "HTML"
    assert "已停止托管" in edited["caption"]
    assert "暂未删除" in edited["caption"]
    setup.delete_message.side_effect = None
    await tracker.tick(setup)
    assert get_card(card.id, owner_id=7).message_deleted
    execution.command.assert_awaited_once()


@pytest.mark.asyncio
async def test_expired_unordered_card_deletes_without_price_and_open_card_has_no_timer(
    setup,
):
    unopened = await published(setup)
    unopened.expires_at = 0
    store.get_store().save(unopened)
    execution.current_price.side_effect = RuntimeError("unavailable")
    await tracker.tick(setup)
    assert get_card(unopened.id, owner_id=7).status == CardStatus.EXPIRED
    execution.current_price.assert_not_awaited()
    opened = await reviewed(setup)
    opened = await action(setup, opened, "confirm")
    opened.expires_at = 0
    store.get_store().save(opened)
    await tracker.tick(setup)
    assert get_card(opened.id, owner_id=7).status == CardStatus.OPEN
    execution.command.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_ttl_cancels_and_retains_partial_fill(setup):
    card = await reviewed(setup)
    execution.open_group.side_effect = lambda card: group(
        card, "PENDING", filled_amount="0"
    )
    card = await action(setup, card, "confirm")
    card.expires_at = 0
    store.get_store().save(card)
    execution.read_group.side_effect = lambda card: group(
        card, "PENDING", filled_amount="0"
    )
    execution.command.side_effect = lambda card: group(
        card,
        "OPEN",
        filled_amount="0.2",
        remaining_amount="0.2",
        operation={"id": card.operation_id, "status": "COMPLETED"},
    )
    await tracker.tick(setup)
    result = get_card(card.id, owner_id=7)
    assert result.status == CardStatus.OPEN
    assert result.execution["filled_amount"] == "0.2"
    setup.delete_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_partial_fill_can_cancel_entries_once_and_keep_protection(setup):
    card = await reviewed(setup)
    execution.open_group.side_effect = lambda card: group(
        card,
        filled_amount="0.2",
        remaining_amount="0.2",
        entry_closed=False,
        available_actions=["cancel_entry", "close", "reduce_half", "detach"],
    )
    card = await action(setup, card, "confirm")
    assert any(
        button.callback_data == f"tc:cancel_entry:{card.id}"
        for row in render.keyboard(card).inline_keyboard
        for button in row
    )
    with pytest.raises(ValueError, match="不属于当前用户"):
        await service.act(
            card.id, "cancel_entry", owner_id=8, chat_id=7, message_id=11, bot=setup
        )
    execution.command.assert_not_awaited()
    commands = []

    def cancel_entry(card):
        commands.append((card.operation_id, card.pending_action))
        return group(
            card,
            filled_amount="0.3",  # A fill races the cancellation acknowledgement.
            remaining_amount="0.3",
            entry_closed=True,
            available_actions=["cancel_entry", "close", "reduce_half", "detach"],
            operation={"id": card.operation_id, "status": "COMPLETED"},
        )

    execution.command.side_effect = cancel_entry
    results = await asyncio.gather(
        action(setup, card, "cancel_entry"),
        action(setup, card, "cancel_entry"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, Card) for result in results) == 1
    execution.command.assert_awaited_once()
    assert commands == [(f"{card.id}-1", "cancel_entry")]
    kept = get_card(card.id, owner_id=7)
    assert kept.status == CardStatus.OPEN
    assert kept.execution["remaining_amount"] == "0.3"
    assert all(
        button.callback_data != f"tc:cancel_entry:{card.id}"
        for row in render.keyboard(kept).inline_keyboard
        for button in row
    )
    setup.delete_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "denied_action", ["cancel_entry", "reduce_half", "close", "detach"]
)
async def test_open_controls_and_confirmation_obey_api_capabilities(
    setup, denied_action
):
    card = await reviewed(setup)
    card = await action(setup, card, "confirm")
    if denied_action != "cancel_entry":
        card = await action(setup, card, denied_action)
        assert card.confirm_action == denied_action
    execution.read_group.side_effect = lambda card: group(
        card,
        entry_closed=False,
        available_actions=[
            action
            for action in ("cancel_entry", "reduce_half", "close", "detach")
            if action != denied_action
        ],
    )
    await tracker.tick(setup)
    refreshed = get_card(card.id, owner_id=7)
    assert refreshed.confirm_action is None
    assert all(
        button.callback_data
        not in {f"tc:{denied_action}:{card.id}", f"tc:yes_{denied_action}:{card.id}"}
        for row in render.keyboard(refreshed).inline_keyboard
        for button in row
    )
    for stale_action in (denied_action, f"yes_{denied_action}"):
        with pytest.raises(ValueError):
            await action(setup, card, stale_action)
    execution.command.assert_not_awaited()


@pytest.mark.asyncio
async def test_closed_settlement_recovers_without_reopening_and_retries_failed_edit(
    setup, monkeypatch
):
    card = await reviewed(setup)
    await action(setup, card, "confirm")
    execution.read_group.side_effect = lambda card: group(
        card,
        "CLOSED",
        remaining_amount="0",
        pnl_complete=False,
        settlement_pending=True,
    )
    await tracker.tick(setup)
    closed = get_card(card.id, owner_id=7)
    assert closed.status == CardStatus.CLOSED
    assert "盈亏待核对" in render.caption(closed)
    monkeypatch.setattr(store, "_store", None)
    execution.read_group.side_effect = execution.ExecutionError("missing", status=404)
    await tracker.tick(setup)
    assert get_card(card.id, owner_id=7).status == CardStatus.CLOSED
    execution.open_group.assert_awaited_once()
    execution.command.assert_not_awaited()
    execution.read_group.side_effect = lambda card: group(card, "OPEN")
    await tracker.tick(setup)
    closed = get_card(card.id, owner_id=7)
    assert closed.status == CardStatus.CLOSED
    assert closed.execution["status"] == "CLOSED"
    execution.read_group.side_effect = lambda card: group(
        card,
        "CLOSED",
        remaining_amount="0",
        pnl="4.78",
        pnl_complete=True,
        settlement_pending=False,
    )
    setup.edit_message_caption.side_effect = TimeoutError()
    await tracker.tick(setup)
    settled = get_card(card.id, owner_id=7)
    assert settled.status == CardStatus.CLOSED
    assert settled.execution["pnl_complete"] is True
    assert "+4.78 USDT" in render.result_row(settled)
    setup.edit_message_caption.side_effect = None
    await tracker.tick(setup)
    assert "+4.78 USDT" in setup.edit_message_caption.await_args.kwargs["caption"]
    assert execution.read_group.await_count == 4
    execution.open_group.assert_awaited_once()
    execution.command.assert_not_awaited()
    setup.delete_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_closed_result_keeps_photo_and_unchanged_tick_does_not_edit(setup):
    card = await reviewed(setup)
    await action(setup, card, "confirm")
    execution.read_group.side_effect = lambda card: group(
        card, "CLOSED", pnl="3.42", close_reason="TP2", remaining_amount="0"
    )
    await tracker.tick(setup)
    closed = get_card(card.id, owner_id=7)
    assert closed.status == CardStatus.CLOSED
    kb = render.keyboard(closed)
    assert len(kb.inline_keyboard) == len(kb.inline_keyboard[0]) == 1
    assert kb.inline_keyboard[0][0].callback_data == f"tc:noop:{card.id}"
    assert "止盈2" in render.caption(closed)
    count = setup.edit_message_caption.await_count
    await tracker.tick(setup)
    assert setup.edit_message_caption.await_count == count
    setup.delete_message.assert_not_awaited()


@pytest.mark.parametrize("value", ["0", "-1", "nan", "Infinity", "", "abc"])
def test_invalid_margin(value):
    with pytest.raises(ValueError):
        margin_amount(value)


def test_preference_remembers_custom_margin_without_clearing_other_sections():
    prefs = {}
    assert get_trade_card_margin(prefs) == "10"
    set_trade_card_margin(prefs, "12.50")
    assert get_trade_card_margin(prefs) == "12.5"
    assert "clob" in prefs["user_preferences"]


def test_transitions_reject_reopening_terminal_card():
    card = Card(
        id="abc",
        plan=plan(),
        owner_id=7,
        chat_id=7,
        server_name="a",
        account_name="a",
        connector_name="binance_perpetual",
        expires_at=100,
    )
    card.transition(CardStatus.REJECTED)
    with pytest.raises(ValueError):
        card.transition(CardStatus.PLACING)


@pytest.mark.asyncio
async def test_caption_reports_estimates_expiry_and_incomplete_results(setup):
    card = await published(setup)
    card.plan = plan(score=87)
    proposed = render.caption(card)
    assert "清晰度  <b>87/100</b>" in proposed and "UTC+8" in proposed
    await action(setup, card, "invest")
    card = await action(setup, card, "amount", margin="10")
    text = render.caption(card)
    assert "止损亏损" in text and "预计强平价" in text and "手续费" in text
    card.execution = {"close_reason": "MANUAL_CLOSE", "pnl_complete": False, "pnl": "0"}
    assert render.result_row(card) == "手动平仓 · 盈亏待核对"
    card.execution = {
        "close_reason": "TAKE_PROFIT",
        "pnl": "3",
        "pnl_complete": True,
        "legs": [{"level_id": f"{card.id}:2", "close_reason": "TAKE_PROFIT"}],
    }
    assert "止盈2" in render.result_row(card)


@pytest.mark.asyncio
async def test_data_outage_keeps_confirmed_risk_reducing_actions(setup):
    card = await reviewed(setup)
    await action(setup, card, "confirm")
    execution.read_group.side_effect = lambda card: group(
        card,
        "RECONCILING",
        price_available=False,
        available_actions=["close", "cancel_entry"],
    )
    await tracker.tick(setup)
    recovering = get_card(card.id, owner_id=7)
    assert any(
        button.callback_data == f"tc:close:{card.id}"
        for row in render.keyboard(recovering).inline_keyboard
        for button in row
    )
    await action(setup, card, "close")
    await tracker.tick(setup)
    assert get_card(card.id, owner_id=7).confirm_action == "close"
    execution.command.assert_not_awaited()
    execution.command.side_effect = lambda card: group(card, "CLOSED")
    closed = await action(setup, card, "yes_close")
    assert closed.status == CardStatus.CLOSED
    unknown = await reviewed(setup)
    execution.open_group.side_effect = execution.ExecutionError("unknown")
    unknown = await action(setup, unknown, "confirm")
    assert all(
        button.callback_data == f"tc:refresh:{unknown.id}"
        for row in render.keyboard(unknown).inline_keyboard
        for button in row
    )


@pytest.mark.asyncio
async def test_live_liquidation_and_negative_net_target_profit_are_not_fabricated(
    setup,
):
    from condor.trade_cards.sizing import validate_quote

    card = await reviewed(setup)
    card.quote = validate_quote(quote(profit_tp1="-0.02"))
    assert "<b>-0.02 / " in render.caption(card)
    card.status = CardStatus.OPEN
    card.mode = "live"
    card.execution = group(
        card,
        liquidation_price="93",
        liquidation_price_kind="exchange",
        pnl_complete=False,
    )
    assert "交易所强平价  <b>93</b>" in render.caption(card)
    assert "盈亏数据尚未完整核对" in render.caption(card)


@pytest.mark.asyncio
async def test_explicit_symbol_rejection_requires_another_confirmation(setup):
    card = await reviewed(setup)
    execution.open_group.side_effect = execution.ExecutionError(
        "busy", status=409, code="SYMBOL_BUSY"
    )
    rejected = await action(setup, card, "confirm")
    assert rejected.status == CardStatus.REVIEW
    assert rejected.create_payload is None
    await tracker.tick(setup)
    execution.open_group.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_telegram_routes_keep_custom_input_on_original_photo_and_clear_it(
    setup, monkeypatch, tmp_path
):
    import config_manager
    import handlers.agents
    import utils.auth as auth
    from handlers import clear_all_input_states
    from handlers.routes import register_handlers
    from tests.support.telegram_offline import message_update, offline_app, update_from

    manager = SimpleNamespace(
        get_user_role=lambda _: config_manager.UserRole.USER,
        get_user_preferences=lambda _: {},
        set_user_preference=Mock(),
    )
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: manager)
    monkeypatch.setattr(auth, "get_config_manager", lambda: manager)
    monkeypatch.setattr(auth, "_capture_identity", Mock())
    monkeypatch.setattr(auth, "_refresh_active_server", Mock())
    agent = AsyncMock()
    monkeypatch.setattr(handlers.agents, "agent_message_handler", agent)
    app = offline_app(tmp_path)
    errors = []

    async def on_error(update, context):
        errors.append(context.error)

    app.error_handler = on_error
    register_handlers()
    await app.initialize()
    card = await published(app.bot)

    async def tap(action_name, user_id=7):
        await app.feed(
            update_from(
                app.bot,
                {
                    "update_id": 20,
                    "callback_query": {
                        "id": "query",
                        "chat_instance": "chat",
                        "data": f"tc:{action_name}:{card.id}",
                        "from": {
                            "id": user_id,
                            "is_bot": False,
                            "first_name": "Tester",
                        },
                        "message": {
                            "message_id": card.message_id,
                            "date": 1,
                            "chat": {"id": 7, "type": "private"},
                        },
                    },
                },
            )
        )
        assert not errors

    try:
        await tap("invest", user_id=8)
        assert get_card(card.id, owner_id=7).status == CardStatus.PROPOSED
        await tap("invest")
        await tap("custom")
        state = app.store.get(7)
        assert state["trade_card_amount_input"]["card_id"] == card.id
        sent_before = sum(
            method == "sendMessage" for method, _ in app.bot.session.requests
        )
        await app.feed(message_update(app.bot, {"text": "not a number"}))
        assert not errors
        assert (
            sum(method == "sendMessage" for method, _ in app.bot.session.requests)
            == sent_before
        )
        assert "请输入有效" in get_card(card.id, owner_id=7).note
        await app.feed(message_update(app.bot, {"text": "12.5"}))
        assert get_card(card.id, owner_id=7).status == CardStatus.REVIEW
        assert get_trade_card_margin(state) == "12.5"
        assert "trade_card_amount_input" not in state
        await tap("invest")
        await tap("custom")
        await tap("back")
        assert get_card(card.id, owner_id=7).status == CardStatus.SIZING
        assert not get_card(card.id, owner_id=7).custom_input
        assert "trade_card_amount_input" not in state
        await tap("custom")
        clear_all_input_states(SimpleNamespace(user_data=state))
        assert not get_card(card.id, owner_id=7).custom_input
        await tracker.tick(app.bot)
        await tap("custom")
        await app.feed(
            message_update(
                app.bot,
                {
                    "text": "/cancel",
                    "entities": [{"type": "bot_command", "offset": 0, "length": 7}],
                },
            )
        )
        assert not errors
        assert "trade_card_amount_input" not in state
        assert not get_card(card.id, owner_id=7).custom_input
        agent.assert_not_awaited()
        photos = [
            fields
            for method, fields in app.bot.session.requests
            if method == "sendPhoto"
        ]
        edits = [
            fields
            for method, fields in app.bot.session.requests
            if method == "editMessageCaption"
        ]
        assert len(photos) == 1
        assert all(fields["message_id"] == str(card.message_id) for fields in edits)
    finally:
        await app.close()


@pytest.mark.asyncio
async def test_tracker_start_and_handler_reload_preserve_single_task(
    setup, monkeypatch
):
    import handlers.trade_cards
    from condor.reload import _EXTRA_RELOAD_MODULES, _discover_handler_modules

    monkeypatch.setattr(tracker, "tick", AsyncMock())
    await tracker.start_tracker(setup)
    task = tracker._task
    importlib.reload(handlers.trade_cards)
    await tracker.start_tracker(setup)
    assert tracker._task is task
    assert not any(
        name.startswith("condor.trade_cards")
        for name in (*_EXTRA_RELOAD_MODULES, *_discover_handler_modules())
    )
    await tracker.stop_tracker()
    assert task.done()
