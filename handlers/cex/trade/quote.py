"""Quote the current trade parameters."""

import asyncio
import logging

from condor.fetchers.market_data import fetch_current_price
from condor.telegram.types import (
    Context,
    Update,
)
from config_manager import get_client
from utils.telegram_formatters import (
    format_error_message,
)

from .._shared import (
    set_cached,
)
from .menu import show_trade_menu

logger = logging.getLogger(__name__)


async def handle_trade_get_quote(update: Update, context: Context) -> None:
    """Get BUY/SELL quotes using get_price_for_volume"""
    params = context.user_data.get("trade_params", {})
    connector = params.get("connector")
    trading_pair = params.get("trading_pair")
    amount = params.get("amount", "1")

    if not all([connector, trading_pair]):
        error_message = format_error_message("Missing connector or trading pair")
        await update.callback_query.message.edit_text(
            error_message, parse_mode="MarkdownV2"
        )
        return

    # Show loading immediately with current menu data
    loading_quote = {"loading": True, "trading_pair": trading_pair, "amount": amount}
    set_cached(context.user_data, "trade_quote", loading_quote)
    await show_trade_menu(update, context, quote_data=loading_quote, auto_fetch=False)

    try:
        # Parse amount (remove $ if present)
        volume = float(str(amount).replace("$", ""))

        chat_id = update.effective_chat.id
        client = await get_client(chat_id, user_data=context.user_data)

        # If amount is in USD, we need to convert to base token volume
        if "$" in str(amount):
            current_price = (
                await fetch_current_price(client, connector, trading_pair, strict=True)
                or 1
            )
            volume = volume / current_price

        # Fetch BUY and SELL quotes in parallel
        async def get_quote_safe(is_buy: bool):
            try:
                result = await client.market_data.get_price_for_volume(
                    connector_name=connector,
                    trading_pair=trading_pair,
                    volume=volume,
                    is_buy=is_buy,
                )
                logger.debug(
                    f"Quote result for {'BUY' if is_buy else 'SELL'}: {result}"
                )
                if isinstance(result, dict):
                    price = (
                        result.get("result_price")
                        or result.get("price")
                        or result.get("average_price")
                        or result.get("data", {}).get("price")
                        or result.get("data", {}).get("result_price")
                    )
                    return float(price) if price else None
                return None
            except Exception as e:
                logger.warning(f"Quote failed for {'BUY' if is_buy else 'SELL'}: {e}")
                return None

        buy_price, sell_price = await asyncio.gather(
            get_quote_safe(True), get_quote_safe(False)
        )

        # If quotes failed, try using get_prices as fallback
        if buy_price is None and sell_price is None:
            logger.info("get_price_for_volume failed, trying get_prices fallback")
            try:
                mid_price = await fetch_current_price(client, connector, trading_pair)
                if mid_price:
                    buy_price = mid_price * 1.0005
                    sell_price = mid_price * 0.9995
            except Exception as e:
                logger.warning(f"Fallback price fetch failed: {e}")

        if buy_price is None and sell_price is None:
            raise ValueError("No quotes available")

        # Build quote data
        quote_data = {
            "trading_pair": trading_pair,
            "amount": volume,
            "buy_price": buy_price,
            "sell_price": sell_price,
        }

        # Cache the quote and update menu
        set_cached(context.user_data, "trade_quote", quote_data)
        await show_trade_menu(update, context, quote_data=quote_data, auto_fetch=False)

    except Exception as e:
        logger.error(f"Error getting quote: {e}", exc_info=True)
        # Show error in quote section
        error_quote = {"error": str(e), "trading_pair": trading_pair}
        set_cached(context.user_data, "trade_quote", error_quote)
        await show_trade_menu(update, context, quote_data=error_quote, auto_fetch=False)
