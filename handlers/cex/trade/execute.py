"""Place the order."""

import logging

from condor.fetchers.market_data import fetch_current_price
from condor.preferences import (
    get_clob_account,
    set_clob_last_order,
)
from condor.server_data_service import ServerDataType, get_server_data_service
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from config_manager import get_client
from utils import telegram_ui as ui
from utils.telegram_formatters import (
    escape_markdown_v2,
    format_error_message,
)

from .._shared import (
    invalidate_cache,
)

logger = logging.getLogger(__name__)


async def handle_trade_execute(update: Update, context: Context) -> None:
    """Execute the trade with current parameters"""
    query = update.callback_query
    if query and not ui.is_current_menu(context.user_data, "cex_trade", query.message):
        await ui.refuse_stale_menu(query, "cex:trade")
        return
    try:
        params = context.user_data.get("trade_params", {})
        account = get_clob_account(context.user_data)

        connector = params.get("connector")
        trading_pair = params.get("trading_pair")
        side = params.get("side")
        amount = params.get("amount")
        order_type = params.get("order_type")
        price = params.get("price")
        position_action = params.get("position_mode", "OPEN")

        if not all([connector, trading_pair, side, amount, order_type]):
            raise ValueError("Missing required parameters")

        if order_type in ["LIMIT", "LIMIT_MAKER"] and (not price or price == "—"):
            raise ValueError("Price required for LIMIT orders")

        chat_id = update.effective_chat.id
        client = await get_client(chat_id, user_data=context.user_data)

        # Handle USD amount
        is_quote_amount = "$" in str(amount)
        if is_quote_amount:
            usd_value = float(str(amount).replace("$", ""))
            current_price = await fetch_current_price(
                client, connector, trading_pair, strict=True
            )
            amount_float = usd_value / current_price
        else:
            amount_float = float(amount)

        result = await client.trading.place_order(
            account_name=account,
            connector_name=connector,
            trading_pair=trading_pair,
            trade_type=side,
            amount=amount_float,
            order_type=order_type,
            price=(
                float(price)
                if price and order_type in ["LIMIT", "LIMIT_MAKER"]
                else None
            ),
            position_action=position_action,
        )

        # Invalidate cache and flag for refresh on next fetch
        invalidate_cache(context.user_data, "balances", "orders", "positions")
        context.user_data["_force_cex_balance_refresh"] = True
        # Also invalidate SDS cache (server-scoped)
        from condor.preferences import get_active_server

        exec_server = get_active_server(context.user_data)
        if exec_server:
            get_server_data_service().invalidate(
                exec_server,
                ServerDataType.PORTFOLIO,
                ServerDataType.ACTIVE_ORDERS,
                ServerDataType.POSITIONS,
            )

        # Save for quick repeat
        set_clob_last_order(
            context.user_data,
            {
                "connector": connector,
                "trading_pair": trading_pair,
                "side": side,
                "order_type": order_type,
                "position_mode": position_action,
                "amount": amount,
                "price": price if price else "—",
            },
        )

        order_info = escape_markdown_v2(
            f"✅ Order placed!\n\n"
            f"Pair: {trading_pair}\n"
            f"Side: {side}\n"
            f"Amount: {amount_float:.6f}\n"
            f"Type: {order_type}"
        )

        if price and order_type in ["LIMIT", "LIMIT_MAKER"]:
            order_info += escape_markdown_v2(f"\nPrice: {price}")

        if "order_id" in result:
            order_info += escape_markdown_v2(f"\nOrder ID: {result['order_id']}")

        keyboard = [
            [InlineKeyboardButton(text="« Back to Trade", callback_data="cex:trade")]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        await update.callback_query.message.edit_text(
            order_info, parse_mode="MarkdownV2", reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error executing trade: {e}", exc_info=True)
        error_message = format_error_message(f"Failed: {str(e)}")
        await update.callback_query.message.edit_text(
            error_message, parse_mode="MarkdownV2"
        )
