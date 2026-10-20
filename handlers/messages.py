"""Route ordinary Telegram input once, with explicit form and chat boundaries."""

from itertools import chain

from condor.telegram.input import InputError, input_has_attachments, message_text
from condor.telegram.types import Context, Update
from utils.auth import restricted

_CEX_INPUT_STATES = {
    "trade",
    "trade_set_pair",
    "trade_set_amount",
    "trade_set_price",
    "trade_set_leverage",
}
_EXECUTOR_INPUT_STATES = {"wizard_pair_input", "wizard_config_input"}
_GATEWAY_INPUT_KEYS = (
    "awaiting_gateway_input",
    "awaiting_wallet_input",
    "awaiting_network_input",
    "awaiting_connector_input",
    "awaiting_token_input",
    "awaiting_pool_input",
    "awaiting_rpc_input",
)


async def message_handler(update: Update, context: Context) -> None:
    """Ignore ordinary group chat while retaining active forms.

    Receives new private and group messages that are neither commands nor
    service updates; :mod:`condor.telegram.app` filters those out.
    """
    if update.message is None:
        return
    if update.effective_chat.type != "private":
        uploading = context.user_data.get("bots_state") == "awaiting_config_upload"
        if not uploading and next(_text_form_handlers(context.user_data), None) is None:
            return
    await _route_message(update, context)


@restricted
async def _route_message(update: Update, context: Context) -> None:
    """Authorize before attachment reads and preserve explicit form priority."""
    message = update.message
    if context.user_data.get("bots_state") == "awaiting_config_upload":
        from handlers.bots import handle_config_file_upload

        if message.document:
            await handle_config_file_upload(update, context)
        else:
            await message.answer(
                "Send the requested configuration file, or use /cancel."
            )
        return

    forms = _text_form_handlers(context.user_data)
    first = next(forms, None)
    if first is not None:
        try:
            text = "" if input_has_attachments(message) else message_text(message)
        except InputError as exc:
            await message.answer(exc.user_message)
            return
        if not text:
            await message.answer(
                "Please send text for the current input, or use /cancel before sending attachments."
            )
            return
        form_update = update
        if text != message.text:
            # Existing form processors read Message.text. Normalize a copy so
            # the original update remains intact for conversational fallthrough.
            form_update = update.with_text(text)
        for handler in chain((first,), forms):
            if await handler(form_update, context) is not False:
                return

    if update.effective_chat.type == "private":
        from handlers.agents import agent_message_handler

        await agent_message_handler(update, context)


def _text_form_handlers(user_data):
    """Yield only active text forms in existing trading/configuration priority.

    Navigation state is not a prompt. Reuse a module's actual dispatch table or
    extension registration where one exists, so opening a menu cannot capture
    the user's next question or image.
    """
    if user_data.get("cex_state") in _CEX_INPUT_STATES:
        from handlers.cex import cex_message_handler

        yield cex_message_handler

    dex_state = user_data.get("dex_state")
    if dex_state:
        from handlers.dex import dex_message_handler
        from handlers.dex.router import MESSAGE_STATE_HANDLERS

        if dex_state in MESSAGE_STATE_HANDLERS:
            yield dex_message_handler

    bots_state = user_data.get("bots_state")
    if bots_state:
        from handlers.bots import bots_message_handler, get_text_input_handler

        if get_text_input_handler(bots_state) is not None:
            yield bots_message_handler

    if user_data.get("executors_state") in _EXECUTOR_INPUT_STATES:
        from handlers.executors import executors_message_handler

        yield executors_message_handler

    if user_data.get("awaiting_add_server_input") or user_data.get(
        "awaiting_modify_input"
    ):
        from handlers.config.servers import handle_server_input

        yield handle_server_input

    if any(user_data.get(key) for key in _GATEWAY_INPUT_KEYS):
        from handlers.config.gateway import handle_gateway_input

        yield handle_gateway_input

    routines_state = user_data.get("routines_state")
    if routines_state:
        from handlers.routines import routines_message_handler
        from routines.base import get_routine_by_state

        routine = (
            get_routine_by_state(routines_state)
            if routines_state not in {"editing", "daily_time"}
            else None
        )
        if routines_state in {"editing", "daily_time"} or (
            routine is not None and routine.message_handler is not None
        ):
            yield routines_message_handler

    if user_data.get("awaiting_share_user_id"):
        from handlers.config.servers import handle_share_user_id_input

        yield handle_share_user_id_input
