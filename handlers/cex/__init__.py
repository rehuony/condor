"""
CEX Trading command handlers - Centralized Exchange trading

Supports:
- Spot & Perpetual exchanges (Binance, Bybit, etc.)
- Place orders (Market/Limit)
- Set leverage & position mode
- Search orders & positions
- Quick trading with saved parameters
"""

import logging
from functools import partial
from typing import Awaitable, Callable

from condor.telegram.types import (
    Context,
    Update,
)
from handlers import clear_all_input_states
from handlers._routing import HandlerFunc, PrefixRoute, dispatch_action, noop
from utils.auth import hummingbot_api_required, restricted
from utils.telegram_formatters import format_error_message

from .menu import cancel_cex_loading_task
from .orders import (
    handle_cancel_order,
    handle_confirm_cancel_order,
    handle_search_orders,
)
from .positions import (
    handle_close_position,
    handle_confirm_close_position,
    handle_positions,
    handle_trade_position,
)
from .trade import (
    handle_close,
    handle_trade,
    handle_trade_connector_select,
    handle_trade_execute,
    handle_trade_get_quote,
    handle_trade_pair_select,
    handle_trade_refresh,
    handle_trade_set_amount,
    handle_trade_set_connector,
    handle_trade_set_leverage,
    handle_trade_set_pair,
    handle_trade_set_price,
    handle_trade_toggle_pos_mode,
    handle_trade_toggle_position,
    handle_trade_toggle_side,
    handle_trade_toggle_type,
    process_trade,
    process_trade_set_amount,
    process_trade_set_leverage,
    process_trade_set_pair,
    process_trade_set_price,
)

logger = logging.getLogger(__name__)


async def _handle_switch_to_dex(
    update: Update, context: Context, network_id: str
) -> None:
    """Switch from CEX to DEX trading"""
    from condor.preferences import (
        get_dex_swap_defaults,
        set_last_trade_connector,
    )
    from handlers.dex.swap import handle_swap as dex_handle_swap

    # Clear CEX state
    clear_all_input_states(context)

    # Save preference (DEX stores network ID)
    set_last_trade_connector(context.user_data, "dex", network_id)

    # Set up DEX swap params with the selected network
    defaults = get_dex_swap_defaults(context.user_data)
    defaults["network"] = network_id
    context.user_data["swap_params"] = defaults

    # Route to DEX swap menu
    await dex_handle_swap(update, context)


# ============================================
# ROUTING TABLES
# ============================================

#: ``cex:<action>`` callbacks that take no parameter.
SIMPLE_ACTIONS: dict[str, HandlerFunc] = {
    "main_menu": handle_trade,
    "trade": handle_trade,
    "trade_refresh": handle_trade_refresh,
    # Trade parameters
    "trade_toggle_side": handle_trade_toggle_side,
    "trade_toggle_type": handle_trade_toggle_type,
    "trade_toggle_position": handle_trade_toggle_position,
    "trade_set_connector": handle_trade_set_connector,
    "trade_set_pair": handle_trade_set_pair,
    "trade_set_amount": handle_trade_set_amount,
    "trade_set_price": handle_trade_set_price,
    "trade_get_quote": handle_trade_get_quote,
    "trade_execute": handle_trade_execute,
    # Leverage & position mode (perpetual)
    "trade_set_leverage": handle_trade_set_leverage,
    "trade_toggle_pos_mode": handle_trade_toggle_pos_mode,
    # Orders
    "search_orders": partial(handle_search_orders, status="ALL"),
    "search_filled": partial(handle_search_orders, status="FILLED"),
    "search_cancelled": partial(handle_search_orders, status="CANCELLED"),
    # Positions
    "positions": handle_positions,
    # Close / separators
    "close": handle_close,
    "noop": noop,
}

#: ``cex:<prefix><value>`` callbacks: the text after the prefix is parsed and
#: passed as the handler's third argument.
PREFIX_ACTIONS: tuple[PrefixRoute, ...] = (
    ("trade_connector_", handle_trade_connector_select, str),
    ("trade_pair_select_", handle_trade_pair_select, str),
    ("cancel_order:", handle_cancel_order, int),
    ("confirm_cancel:", handle_confirm_cancel_order, int),
    ("trade_position:", handle_trade_position, int),
    ("close_position:", handle_close_position, int),
    ("confirm_close:", handle_confirm_close_position, int),
    ("switch_dex_", _handle_switch_to_dex, str),
)

#: ``cex_state`` values and the processor that consumes the typed text.
TEXT_INPUT_HANDLERS: dict[str, Callable[[Update, Context, str], Awaitable[None]]] = {
    "trade": process_trade,
    "trade_set_pair": process_trade_set_pair,
    "trade_set_amount": process_trade_set_amount,
    "trade_set_price": process_trade_set_price,
    "trade_set_leverage": process_trade_set_leverage,
}

#: States that are removed before processing because the input completes them.
_COMPLETING_STATES = frozenset({"trade"})


@restricted
@hummingbot_api_required
async def trade_command(update: Update, context: Context) -> None:
    """
    Handle /trade command - CEX trading interface with order books

    Usage:
        /trade - Show trading menu with limit/market orders
    """
    # Clear all pending input states to prevent interference
    clear_all_input_states(context)

    # Get the appropriate message object for replies
    msg = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if not msg:
        logger.error("No message object available for trade_command")
        return

    # Send "typing" status
    await msg.bot.send_chat_action(chat_id=msg.chat.id, action="typing")

    # Show unified trade menu
    await handle_trade(update, context)


@restricted
async def cex_callback_handler(update: Update, context: Context) -> None:
    """Handle inline button callbacks for CEX trading operations"""
    query = update.callback_query
    await query.answer()

    await query.message.bot.send_chat_action(
        chat_id=query.message.chat.id, action="typing"
    )

    try:
        callback_parts = query.data.split(":", 1)
        action = callback_parts[1] if len(callback_parts) > 1 else query.data

        # Cancel any pending menu loading task when navigating away
        if action not in ("main_menu", "trade"):
            cancel_cex_loading_task(context)

        if not await dispatch_action(
            update, context, action, SIMPLE_ACTIONS, PREFIX_ACTIONS
        ):
            await query.message.answer(f"Unknown action: {action}")

    except Exception as e:
        logger.error(f"Error in CEX callback handler: {e}", exc_info=True)
        error_message = format_error_message(f"Operation failed: {str(e)}")
        await query.message.answer(error_message, parse_mode="MarkdownV2")


@restricted
async def cex_message_handler(update: Update, context: Context) -> None:
    """Handle user text input for CEX trading operations"""
    cex_state = context.user_data.get("cex_state")

    logger.info(
        f"CEX message handler called. State: {cex_state}, Message: {update.message.text if update.message else 'No message'}"
    )

    if not cex_state:
        logger.info("No cex_state found, returning")
        return

    user_input = update.message.text.strip()
    logger.info(f"Processing input for state: {cex_state}, input: {user_input}")

    try:
        # Only remove state for completing operations
        if cex_state in _COMPLETING_STATES:
            context.user_data.pop("cex_state", None)
            logger.info(f"Removed cex_state for completing operation: {cex_state}")

        processor = TEXT_INPUT_HANDLERS.get(cex_state)
        if processor is None:
            await update.message.answer(f"Unknown state: {cex_state}")
            return
        await processor(update, context, user_input)

    except Exception as e:
        logger.error(f"Error processing CEX input: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to process input: {str(e)}")
        await update.message.answer(error_message, parse_mode="MarkdownV2")
