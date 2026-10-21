"""Telegram adapter for the independent trade-card service."""

from condor.preferences import get_trade_card_margin, set_trade_card_margin
from condor.telegram.types import Context, Update
from condor.trade_cards import get_card
from condor.trade_cards.execution import ExecutionError
from condor.trade_cards.models import CardStatus
from condor.trade_cards.service import act, show_input_error
from handlers import clear_all_input_states
from utils.auth import restricted

INPUT_KEY = "trade_card_amount_input"


@restricted
async def trade_cards_callback_handler(update: Update, context: Context) -> None:
    query = update.callback_query
    parts = (query.data or "").split(":")
    if len(parts) != 3 or not query.message:
        await query.answer("卡片按钮无效。", show_alert=True)
        return
    _, action, card_id = parts
    owner_id, chat_id = update.effective_user.id, update.effective_chat.id
    card = get_card(card_id, owner_id=owner_id)
    if (
        card is None
        or card.chat_id != chat_id
        or card.message_id != query.message.message_id
    ):
        await query.answer("只有卡片接收者可以操作。", show_alert=True)
        return
    await query.answer()
    margin = None
    if action in {"m10", "m20", "m50", "last"}:
        margin = card.margin if action == "last" else action[1:]
        action = "amount"
    elif action == "invest":
        margin = get_trade_card_margin(context.user_data)
    if action in {"invest", "custom"}:
        clear_all_input_states(context)
    try:
        card = await act(
            card_id,
            action,
            owner_id=owner_id,
            chat_id=chat_id,
            message_id=query.message.message_id,
            bot=context.bot,
            margin=margin,
        )
    except (ValueError, ExecutionError) as exc:
        await query.answer(str(exc)[:190], show_alert=True)
        return
    if action == "custom" and card.status == CardStatus.SIZING and card.custom_input:
        context.user_data[INPUT_KEY] = {
            "card_id": card.id,
            "chat_id": card.chat_id,
            "message_id": card.message_id,
        }
    elif action != "noop":
        context.user_data.pop(INPUT_KEY, None)
    if action == "amount" and card.status == CardStatus.REVIEW:
        set_trade_card_margin(context.user_data, card.margin)


async def trade_cards_message_handler(update: Update, context: Context) -> bool:
    pending = context.user_data.get(INPUT_KEY)
    if not pending or pending.get("chat_id") != update.effective_chat.id:
        return False
    try:
        card = await act(
            pending["card_id"],
            "amount",
            owner_id=update.effective_user.id,
            chat_id=update.effective_chat.id,
            message_id=pending.get("message_id"),
            bot=context.bot,
            margin=update.message.text,
        )
    except (ValueError, ExecutionError) as exc:
        current = get_card(pending["card_id"], owner_id=update.effective_user.id)
        if current is None or current.status != CardStatus.SIZING:
            context.user_data.pop(INPUT_KEY, None)
        else:
            await show_input_error(
                current.id,
                str(exc),
                owner_id=update.effective_user.id,
                chat_id=update.effective_chat.id,
                message_id=pending.get("message_id"),
                bot=context.bot,
            )
        return True
    context.user_data.pop(INPUT_KEY, None)
    if card.status == CardStatus.REVIEW:
        set_trade_card_margin(context.user_data, card.margin)
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
