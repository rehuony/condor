"""Telegram adapter for the independent trade-card service."""

import asyncio

from condor.telegram.types import Context, Update
from condor.trade_cards import get_card
from condor.trade_cards.execution import ExecutionError
from condor.trade_cards.models import CardStatus
from condor.trade_cards.service import act
from handlers import clear_all_input_states
from utils.auth import restricted

INPUT_KEY = "trade_card_amount_input"
#: A button may be answered once. An action that finishes within this many
#: seconds answers with its refusal as a popup; a slower one answers at once and
#: its refusal is only on the card, where every refusal is also written.
ANSWER_WITHIN = 5
#: Typed input goes to the step the card is on.
INPUT_ACTIONS = {CardStatus.LEVERAGE: "leverage", CardStatus.SIZING: "amount"}


@restricted
async def trade_cards_callback_handler(update: Update, context: Context) -> None:
    query = update.callback_query
    parts = (query.data or "").split(":")
    if len(parts) != 3 or not query.message:
        await query.answer("This card button is invalid.", show_alert=True)
        return
    _, action, card_id = parts
    owner_id, chat_id = update.effective_user.id, update.effective_chat.id
    card = get_card(card_id, owner_id=owner_id)
    if (
        card is None
        or card.chat_id != chat_id
        or card.message_id != query.message.message_id
    ):
        await query.answer("Only the card's recipient can use it.", show_alert=True)
        return
    value = None
    if action in {"m10", "m20", "m50", "mmin"}:
        value = card.limits.get("min_margin") if action == "mmin" else action[1:]
        action = "amount"
    elif action in {"l10", "l25", "l50", "lmax"}:
        value = (
            str(card.limits.get("max_leverage", "")) if action == "lmax" else action[1:]
        )
        action = "leverage"
    if action in {"invest", "custom"}:
        clear_all_input_states(context)
    task = asyncio.ensure_future(
        act(
            card_id,
            action,
            owner_id=owner_id,
            chat_id=chat_id,
            message_id=query.message.message_id,
            bot=context.bot,
            value=value,
        )
    )
    done, _ = await asyncio.wait({task}, timeout=ANSWER_WITHIN)
    if not done:
        await query.answer()
    refusal = None
    try:
        card = await task
    except (ValueError, ExecutionError) as exc:
        refusal = str(exc)
    finally:
        if done:
            if refusal:
                await query.answer(refusal[:190], show_alert=True)
            else:
                await query.answer()
    if refusal:
        return
    if action == "custom" and card.status in INPUT_ACTIONS and card.custom_input:
        context.user_data[INPUT_KEY] = {
            "card_id": card.id,
            "chat_id": card.chat_id,
            "message_id": card.message_id,
        }
    else:
        context.user_data.pop(INPUT_KEY, None)


async def trade_cards_message_handler(update: Update, context: Context) -> bool:
    pending = context.user_data.get(INPUT_KEY)
    if not pending or pending.get("chat_id") != update.effective_chat.id:
        return False
    owner_id = update.effective_user.id
    current = get_card(pending["card_id"], owner_id=owner_id)
    action = INPUT_ACTIONS.get(current.status) if current else None
    if action is None:
        context.user_data.pop(INPUT_KEY, None)
        return False
    try:
        await act(
            pending["card_id"],
            action,
            owner_id=owner_id,
            chat_id=update.effective_chat.id,
            message_id=pending.get("message_id"),
            bot=context.bot,
            value=update.message.text,
        )
    except (ValueError, ExecutionError):
        # The reason is on the card; the input stays armed for another try.
        current = get_card(pending["card_id"], owner_id=owner_id)
        if current is None or not current.custom_input:
            context.user_data.pop(INPUT_KEY, None)
        return True
    context.user_data.pop(INPUT_KEY, None)
    return True


async def cancel_amount_input(update: Update, context: Context) -> bool:
    pending = context.user_data.pop(INPUT_KEY, None)
    if not pending:
        return False
    try:
        await act(
            pending["card_id"],
            "cancel_custom",
            owner_id=update.effective_user.id,
            chat_id=pending["chat_id"],
            message_id=pending.get("message_id"),
            bot=context.bot,
        )
    except (ValueError, ExecutionError):
        pass
    return True
