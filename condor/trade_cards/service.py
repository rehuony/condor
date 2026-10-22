"""Card publication and serialized user actions, independent of signal sources."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
from decimal import Decimal
from pathlib import Path

from condor.paths import data_dir
from condor.telegram.errors import (
    BadRequest,
    Forbidden,
    is_connect_failure,
    is_not_modified,
)
from condor.telegram.transport import as_input_file

from . import execution, render
from .models import (
    EXECUTING,
    HIDDEN,
    TERMINAL,
    UNORDERED,
    Card,
    CardStatus,
    TradePlan,
    management_actions,
)
from .sizing import margin_amount
from .store import get_store

logger = logging.getLogger(__name__)
TTL_SECONDS = 4 * 3600


class PlanOutdated(ValueError):
    """The live price already invalidates the plan; nothing was published."""


def get_card(card_id: str, *, owner_id: int) -> Card | None:
    card = get_store().get(card_id)
    return card if card and card.owner_id == owner_id else None


def list_cards(*, owner_id: int) -> list[Card]:
    return [card for card in get_store().all() if card.owner_id == owner_id]


def cancel_input_state(pending: dict, *, owner_id: int) -> None:
    """Clear presentation-only input state without scheduling async work."""
    store = get_store()
    card = store.get(pending.get("card_id"))
    if (
        card
        and card.owner_id == owner_id
        and card.chat_id == pending.get("chat_id")
        and card.message_id == pending.get("message_id")
        and card.custom_input
    ):
        card.note = ""
        store.cancel_custom_input(card)


async def publish(
    plan: TradePlan | dict,
    candles,
    *,
    owner_id: int,
    chat_id: int,
    server_name: str,
    account_name: str,
    connector_name: str,
    bot,
    signal_id: str | None = None,
    mode: str = "live",
) -> Card | None:
    """Publish one photo; None means that exact signal was already reserved.

    Call in the Condor main process (routines or run_code). The fixed target and
    recipient scope deduplication across source instances. Ambiguous Telegram
    delivery retains the reservation: replaying it could send a second card.
    """
    from .chart import render_chart

    plan = TradePlan.model_validate(plan)
    if (
        not owner_id
        or not chat_id
        or not all((server_name, account_name, connector_name))
    ):
        raise ValueError("A card requires a recipient and an explicit execution target")
    if not callable(getattr(bot, "send_photo", None)):
        raise ValueError("Trade cards require a Telegram photo sender")
    if mode not in ("live", "mock"):
        raise ValueError("A card's mode must be live or mock")
    manager = execution.authorize_target(owner_id, server_name)
    target_fingerprint = execution.server_fingerprint(manager, server_name)
    signal_key = None
    if signal_id is not None:
        identity = [
            owner_id,
            chat_id,
            server_name,
            account_name,
            connector_name,
            plan.trading_pair,
            plan.side,
            plan.timeframe,
            plan.signal_type,
            signal_id,
            # A mock instance never mutes the live one's signals, or the reverse.
            mode,
        ]
        signal_key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    card = Card(
        id=secrets.token_hex(6),
        plan=plan,
        owner_id=owner_id,
        chat_id=chat_id,
        server_name=server_name,
        server_fingerprint=target_fingerprint,
        account_name=account_name,
        connector_name=connector_name,
        mode=mode,
        signal_key=signal_key,
        expires_at=time.time() + TTL_SECONDS,
    )
    # The tracker expires and deletes such a card on its first tick, so sending
    # it would report a delivery the owner never gets to act on.
    try:
        price = await execution.current_price(card)
    except Exception:
        # Same rule as the tracker: an unavailable price does not expire a card.
        logger.warning("Trade card price unavailable at publish: %s", plan.trading_pair)
    else:
        if reason := invalid_reason(card, price):
            raise PlanOutdated(f"{reason} (price {price})")
    store = get_store()
    async with store.publish_lock:
        if signal_key is not None and store.signal_reserved(signal_key):
            return None
        store.save(card)
    async with store.lock(card.id):
        try:
            png = await asyncio.to_thread(render_chart, candles, plan, connector_name)
            # Kept so a deleted message of a live card can be sent again.
            path = chart_path(card.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(png)
        except Exception:
            card.delivery_status = "failed"
            card.transition(CardStatus.FAILED)
            store.save(card)
            raise
        try:
            message = await bot.send_photo(
                chat_id=chat_id,
                photo=as_input_file(png, filename=f"trade-{card.id}.png"),
                caption=render.caption(card),
                reply_markup=render.keyboard(card),
                parse_mode="MarkdownV2",
            )
            if not getattr(message, "message_id", None):
                raise RuntimeError("Telegram did not confirm the photo message id")
        except Exception as exc:
            definite = isinstance(exc, (BadRequest, Forbidden)) or is_connect_failure(
                exc
            )
            card.delivery_status = "failed" if definite else "unknown"
            card.note = (
                "Sending failed."
                if definite
                else "Delivery is unconfirmed; the card is kept to avoid sending it twice."
            )
            if definite:
                card.transition(CardStatus.FAILED)
            store.save(card)
            raise RuntimeError(f"{card.note} ({card.id})") from exc
        card.message_id = message.message_id
        card.delivery_status = "sent"
        card.last_render = _render_key(card)
        store.save(card)
        return card


def _authorize(
    card_id: str, owner_id: int, chat_id: int, message_id: int | None
) -> Card:
    card = get_store().get(card_id)
    if (
        card is None
        or card.owner_id != owner_id
        or card.chat_id != chat_id
        or (message_id is not None and card.message_id != message_id)
    ):
        raise ValueError("This card does not exist or belongs to someone else.")
    if card.message_deleted:
        raise ValueError("This card has ended.")
    return card


def invalid_reason(
    card: Card, price: Decimal | None = None, *, now: float | None = None
) -> str | None:
    if (time.time() if now is None else now) >= card.expires_at:
        return "The card passed its 4-hour validity."
    if price is not None:
        p = card.plan
        if (p.side == "long" and (price <= p.stop_price or price >= p.tp1_price)) or (
            p.side == "short" and (price >= p.stop_price or price <= p.tp1_price)
        ):
            return "The price already crossed the stop or TP1, so the card expired."
    return None


def _render_key(card: Card) -> str:
    kb = render.keyboard(card)
    return json.dumps(
        [render.caption(card), kb.model_dump(mode="json") if kb else None],
        ensure_ascii=False,
        sort_keys=True,
    )


#: How often an unchanged message is checked for having been deleted: often
#: while the card trades, rarely once it only shows a result.
CHECK_SECONDS = {"trading": 60, "idle": 600}


def chart_path(card_id: str) -> Path:
    return data_dir() / "trade_cards" / "charts" / f"{card_id}.png"


def _purge(card: Card) -> None:
    """Delete the record of a card whose message is gone for good."""
    get_store().delete(card)
    chart_path(card.id).unlink(missing_ok=True)


def _gone(exc: BaseException) -> bool:
    return isinstance(exc, BadRequest) and "not found" in str(exc).lower()


async def _resend(card: Card, bot) -> None:
    """Send a trading card again so it always has a message to track it by."""
    path = chart_path(card.id)
    if not path.exists():
        logger.warning("Trade card %s has no saved chart to resend", card.id)
        return
    try:
        message = await bot.send_photo(
            chat_id=card.chat_id,
            photo=as_input_file(path.read_bytes(), filename=f"trade-{card.id}.png"),
            caption=render.caption(card),
            reply_markup=render.keyboard(card),
            parse_mode="MarkdownV2",
        )
    except Exception:
        # Retried on the next tracker tick.
        logger.warning("Trade card resend failed: %s", card.id)
        return
    card.message_id = message.message_id
    card.last_render = _render_key(card)
    card.last_checked = time.time()
    get_store().save(card)


async def _message_gone(card: Card, bot) -> None:
    """The chat no longer holds the card's message."""
    if card.status in EXECUTING:
        card.message_id = None
        get_store().save(card)
        await _resend(card, bot)
    else:
        _purge(card)


async def update_message(card: Card, bot) -> None:
    """Keep the card's one message current, or retire the card without it.

    A card that is still trading must always have a message: if it was deleted
    it is sent again. Any other card is deleted once its message is gone, so no
    stale record can be acted on later.
    """
    store = get_store()
    if card.message_id is None:
        if card.status in EXECUTING and card.delivery_status == "sent":
            await _resend(card, bot)
        elif card.status in TERMINAL:
            _purge(card)
        return
    if card.message_deleted:
        await _message_gone(card, bot)
        return
    if card.status in HIDDEN:
        try:
            await bot.delete_message(chat_id=card.chat_id, message_id=card.message_id)
        except Exception as exc:
            if _gone(exc):
                _purge(card)
                return
            # Strip the buttons and say why the message is still there; the
            # deletion is retried on the next tick.
            card.delete_failed = True
            logger.warning("Trade card message deletion pending: %s", card.id)
        else:
            _purge(card)
            return
    key = _render_key(card)
    try:
        if key != card.last_render:
            await bot.edit_message_caption(
                chat_id=card.chat_id,
                message_id=card.message_id,
                caption=render.caption(card),
                reply_markup=render.keyboard(card),
                parse_mode="MarkdownV2",
            )
        else:
            interval = CHECK_SECONDS["trading" if card.status in EXECUTING else "idle"]
            if time.time() - card.last_checked < interval:
                return
            # Bots are not told about deletions; an unchanged edit answers
            # "not modified" while the message exists and "not found" once gone.
            await bot.edit_message_reply_markup(
                chat_id=card.chat_id,
                message_id=card.message_id,
                reply_markup=render.keyboard(card),
            )
    except BadRequest as exc:
        if _gone(exc):
            await _message_gone(card, bot)
            return
        if not is_not_modified(exc):
            raise
    card.last_render = key
    card.last_checked = time.time()
    store.save(card)


def _apply_group(card: Card, group: dict) -> None:
    if group.get("id") != card.id or group.get("status") not in {
        s.value
        for s in EXECUTING | {CardStatus.CLOSED, CardStatus.DETACHED, CardStatus.FAILED}
    }:
        raise execution.ExecutionError(
            "The executor's trade record cannot be confirmed; it is being checked."
        )
    card.execution = group
    status = CardStatus(group["status"])
    if (
        status == CardStatus.CLOSED
        and group.get("filled_amount") is not None
        and Decimal(str(group["filled_amount"])) == 0
    ):
        status = CardStatus.CANCELLED
    operation = group.get("operation") or {}
    if card.pending_action:
        if operation.get("id") == card.operation_id and operation.get("status") in {
            "COMPLETED",
            "FAILED",
        }:
            card.pending_action = None
            card.operation_id = None
            card.note = (
                "The action did not complete. Check the position and try again."
                if operation.get("status") == "FAILED"
                else ""
            )
        elif status not in TERMINAL:
            status = CardStatus.RECONCILING
    if status in TERMINAL:
        card.pending_action = None
        card.operation_id = None
    elif operation.get("status") == "PENDING":
        status = CardStatus.RECONCILING
    card.transition(status)
    if card.confirm_action not in management_actions(card):
        card.confirm_action = None
        card.confirm_until = 0
    if group.get("error"):
        card.note = execution.user_error(group["error"])


async def _reconcile(card: Card) -> None:
    try:
        try:
            group = await execution.read_group(card)
        except execution.ExecutionError as exc:
            if exc.status != 404 or card.create_payload is None:
                raise
            # The intent may have been saved just before a process crash. The
            # group API guarantees replay of this exact id/body cannot create
            # another group. Never derive a new payload from mutable UI state.
            group = await execution.open_group(card)
        if card.pending_action and group.get("status") in {s.value for s in EXECUTING}:
            operation = group.get("operation") or {}
            if operation.get("id") != card.operation_id:
                group = await execution.command(card)
        _apply_group(card, group)
        if (
            not group.get("error")
            and not card.pending_action
            and (group.get("operation") or {}).get("status") != "FAILED"
        ):
            card.note = ""
    except execution.ExecutionError as exc:
        if exc.status == 409 and card.pending_action:
            # This operation was definitively rejected, for example because a
            # different operation is still in flight. Do not replay it later.
            card.pending_action = None
            card.operation_id = None
        card.note = (
            "The execution record is unconfirmed; actions are paused until it is checked."
            if exc.status == 404
            else str(exc)
        )
        # A GET failure is no evidence of order absence. Keep the intent and
        # operation id across both network failures and process restarts.
        card.transition(CardStatus.RECONCILING)
        if (
            (exc.status in {400, 422} or exc.code == "SYMBOL_BUSY")
            and not card.execution
            and not card.pending_action
        ):
            card.transition(CardStatus.REVIEW)
            card.create_payload = None
    get_store().save(card)


async def _command(card: Card, action: str) -> None:
    card.operation_seq += 1
    card.operation_id = f"{card.id}-{card.operation_seq}"
    card.pending_action = action
    card.confirm_action = None
    card.confirm_until = 0
    card.transition(CardStatus.CLOSING)
    get_store().save(card)
    try:
        _apply_group(card, await execution.command(card))
    except execution.ExecutionError as exc:
        if exc.status in {400, 409, 422}:
            card.pending_action = None
            card.operation_id = None
        card.transition(CardStatus.RECONCILING)
        card.note = str(exc)
    get_store().save(card)


async def _settle_closed(card: Card) -> None:
    """Refresh final accounting without reopening or replaying a completed trade."""
    try:
        group = await execution.read_group(card)
        if (
            group.get("id") != card.id
            or group.get("status") != CardStatus.CLOSED
            or Decimal(str(group.get("remaining_amount"))) != 0
        ):
            raise execution.ExecutionError(
                "The closed trade's settlement is unconfirmed; checking continues."
            )
    except (execution.ExecutionError, ValueError, ArithmeticError) as exc:
        logger.warning(
            "Trade card settlement unavailable: %s (%s)", card.id, type(exc).__name__
        )
        card.note = "Closed; settlement data is unavailable and checking continues."
    else:
        card.execution = group
        card.note = execution.user_error(group["error"]) if group.get("error") else ""
    get_store().save(card)


async def act(
    card_id: str,
    action: str,
    *,
    owner_id: int,
    chat_id: int,
    message_id: int | None,
    bot,
    margin: str | None = None,
) -> Card:
    """Telegram-facing action boundary. Every mutation holds the same card lock."""
    store = get_store()
    async with store.lock(card_id):
        card = _authorize(card_id, owner_id, chat_id, message_id)
        if action == "noop":
            return card
        if action == "refresh" and card.status in EXECUTING:
            await _reconcile(card)
        elif card.status in UNORDERED:
            if reason := invalid_reason(card):
                card.note = reason
                card.transition(CardStatus.EXPIRED)
            elif action == "reject":
                card.transition(CardStatus.REJECTED)
            elif action == "invest":
                card.transition(CardStatus.SIZING)
                if margin is not None:
                    card.margin = margin_amount(margin)
                card.custom_input = False
                card.note = ""
            elif action == "back" and card.status == CardStatus.SIZING:
                if not card.custom_input:
                    card.transition(CardStatus.PROPOSED)
                card.custom_input = False
                card.note = ""
            elif (
                action in {"custom", "cancel_custom"}
                and card.status == CardStatus.SIZING
            ):
                card.custom_input = action == "custom"
                if card.custom_input:
                    store.allow_custom_input(card.id)
                card.note = ""
            elif action == "amount" and card.status == CardStatus.SIZING:
                card.margin = margin_amount(margin or card.margin)
                card.quote = await execution.preview(card)
                price = Decimal(str(card.quote["current_price"]))
                if reason := invalid_reason(card, price):
                    card.note = reason
                    card.transition(CardStatus.EXPIRED)
                else:
                    card.transition(CardStatus.REVIEW)
                    card.custom_input = False
                    card.note = ""
            elif action == "confirm" and card.status == CardStatus.REVIEW:
                # Preview itself cannot place an order. Capability and quote
                # errors therefore leave REVIEW available for a later retry.
                card.quote = await execution.preview(card)
                if reason := invalid_reason(
                    card, Decimal(str(card.quote["current_price"]))
                ):
                    card.note = reason
                    card.transition(CardStatus.EXPIRED)
                else:
                    card.create_payload = execution.payload(card)
                    card.transition(CardStatus.PLACING)
                    card.note = ""
                    store.save(card)
                    try:
                        await update_message(card, bot)
                    except Exception:
                        # Telegram is a view, never the execution transaction.
                        logger.warning("Could not show placing state: %s", card.id)
                    try:
                        _apply_group(card, await execution.open_group(card))
                    except execution.ExecutionError as exc:
                        card.transition(CardStatus.RECONCILING)
                        card.note = str(exc)
                        # A definitive schema/risk rejection did not create a
                        # group; unknown failures must never return to REVIEW.
                        if exc.status in {400, 422} or exc.code == "SYMBOL_BUSY":
                            card.transition(CardStatus.REVIEW)
                            card.create_payload = None
            else:
                raise ValueError("The card changed. Use the buttons it shows now.")
        elif action == "cancel_entry" and action in management_actions(card):
            await _command(card, action)
        elif allowed := management_actions(card):
            if action in allowed:
                card.confirm_action = action
                card.confirm_until = time.time() + 30
            elif action == "back":
                card.confirm_action = None
            elif (
                action.startswith("yes_")
                and card.confirm_action == action[4:]
                and action[4:] in allowed
                and time.time() <= card.confirm_until
            ):
                await _command(card, action[4:])
            else:
                raise ValueError("The confirmation expired. Choose again.")
        else:
            raise ValueError(
                "The card is busy or has ended. Refresh to check its status."
            )
        store.save(card)
        await update_message(card, bot)
        return card


async def refresh_card(card_id: str, bot) -> None:
    store = get_store()
    async with store.lock(card_id):
        card = store.get(card_id)
        if card is None:
            return
        if card.status in UNORDERED:
            reason = invalid_reason(card)
            if not reason:
                try:
                    reason = invalid_reason(card, await execution.current_price(card))
                except Exception:
                    # Unavailable price is not zero and cannot expire a card.
                    logger.warning("Trade card price unavailable: %s", card.id)
            if reason:
                card.note = reason
                card.transition(CardStatus.EXPIRED)
                store.save(card)
        elif card.status in EXECUTING:
            await _reconcile(card)
            if (
                card.status == CardStatus.PENDING
                and time.time() >= card.expires_at
                and not card.pending_action
            ):
                await _command(card, "cancel_entry")
        elif card.status == CardStatus.CLOSED and (
            card.execution.get("settlement_pending") is True
            or card.execution.get("pnl_complete") is False
        ):
            await _settle_closed(card)
        if card.confirm_action and time.time() > card.confirm_until:
            card.confirm_action = None
            store.save(card)
        await update_message(card, bot)


async def show_input_error(
    card_id: str, message: str, *, owner_id: int, chat_id: int, message_id: int, bot
) -> None:
    """Show custom-amount validation in the existing photo, under its lock."""
    store = get_store()
    async with store.lock(card_id):
        card = _authorize(card_id, owner_id, chat_id, message_id)
        if card.status == CardStatus.SIZING:
            card.note = message
            store.save(card)
            await update_message(card, bot)
