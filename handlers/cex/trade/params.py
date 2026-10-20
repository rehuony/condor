"""Trade parameter handlers: side, type, connector, pair, amount, price, leverage, position mode."""

import asyncio
import logging

from condor.preferences import (
    get_all_enabled_networks,
    get_clob_account,
    set_last_trade_connector,
)
from condor.server_data_service import ServerDataType, get_server_data_service
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from config_manager import get_client
from utils.telegram_formatters import (
    escape_markdown_v2,
    format_error_message,
)

from .._shared import (
    get_available_cex_connectors,
    invalidate_cache,
)
from .helpers import (
    DEFAULT_LEVERAGE,
    _get_leverage_cache_key,
    _get_position_mode_cache_key,
)
from .menu import show_trade_menu

logger = logging.getLogger(__name__)


def _invalidate_trade_cache(user_data: dict) -> None:
    """Invalidate cached price/quote when params change"""
    user_data.pop("current_market_price", None)
    # Also clear quote cache
    cache = user_data.get("_cache", {})
    if "trade_quote" in cache:
        del cache["trade_quote"]


async def handle_trade_toggle_side(update: Update, context: Context) -> None:
    """Toggle between BUY and SELL"""
    params = context.user_data.get("trade_params", {})
    current_side = params.get("side", "BUY")
    params["side"] = "SELL" if current_side == "BUY" else "BUY"
    await show_trade_menu(update, context, auto_fetch=False)


async def handle_trade_toggle_type(update: Update, context: Context) -> None:
    """Toggle between MARKET, LIMIT, LIMIT_MAKER"""
    params = context.user_data.get("trade_params", {})
    current_type = params.get("order_type", "MARKET")

    if current_type == "MARKET":
        params["order_type"] = "LIMIT"
        # Set default price from current market price
        current_price = context.user_data.get("current_market_price")
        if current_price:
            params["price"] = str(current_price)
        else:
            params["price"] = "—"
    elif current_type == "LIMIT":
        params["order_type"] = "LIMIT_MAKER"
    else:
        params["order_type"] = "MARKET"

    await show_trade_menu(update, context, auto_fetch=False)


async def handle_trade_toggle_position(update: Update, context: Context) -> None:
    """Toggle between OPEN and CLOSE"""
    params = context.user_data.get("trade_params", {})
    current_mode = params.get("position_mode", "OPEN")
    params["position_mode"] = "CLOSE" if current_mode == "OPEN" else "OPEN"
    await show_trade_menu(update, context, auto_fetch=False)


async def handle_trade_set_connector(update: Update, context: Context) -> None:
    """Show available CEX connectors and DEX networks for selection"""
    from handlers.bots._shared import resolve_accessible_server_or_none

    chat_id = update.effective_chat.id
    keyboard = []

    # Get server name for cache keying - with proper access control. Only a
    # cache-key component, so the historical "default" literal is kept for the
    # no-accessible-server case rather than poisoning keys with None.
    server_name = resolve_accessible_server_or_none(context.user_data) or "default"

    try:
        client = await get_client(chat_id, user_data=context.user_data)

        # Fetch CEX connectors and DEX networks

        async def get_cex_connectors_list():
            try:
                return await get_available_cex_connectors(
                    context.user_data, client, server_name=server_name
                )
            except Exception as e:
                logger.warning(f"Could not fetch CEX connectors: {e}")
                return []

        async def get_dex_networks():
            try:
                response = await client.gateway.list_networks()
                all_networks = response.get("networks", [])

                # Filter to only show networks enabled in user's wallet configurations
                enabled_networks = get_all_enabled_networks(context.user_data)
                if enabled_networks is None:
                    # No wallets configured, show all networks
                    return all_networks

                # Filter networks to only those enabled for user's wallets
                filtered = []
                for network_item in all_networks:
                    if isinstance(network_item, dict):
                        network_id = (
                            network_item.get("network_id")
                            or network_item.get("id")
                            or str(network_item)
                        )
                    else:
                        network_id = str(network_item)

                    if network_id in enabled_networks:
                        filtered.append(network_item)

                return filtered
            except Exception as e:
                logger.warning(f"Could not fetch DEX networks: {e}")
                return []

        cex_connectors, dex_networks = await asyncio.gather(
            get_cex_connectors_list(), get_dex_networks()
        )

        # CEX section
        if cex_connectors:
            keyboard.append(
                [InlineKeyboardButton(text="━━ CEX ━━", callback_data="cex:noop")]
            )
            row = []
            for connector in cex_connectors:
                row.append(
                    InlineKeyboardButton(
                        text=connector, callback_data=f"cex:trade_connector_{connector}"
                    )
                )
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)

        # DEX section
        if dex_networks:
            keyboard.append(
                [InlineKeyboardButton(text="━━ DEX ━━", callback_data="cex:noop")]
            )
            row = []
            for network_item in dex_networks:
                if isinstance(network_item, dict):
                    network_id = (
                        network_item.get("network_id")
                        or network_item.get("id")
                        or str(network_item)
                    )
                else:
                    network_id = str(network_item)
                row.append(
                    InlineKeyboardButton(
                        text=network_id, callback_data=f"cex:switch_dex_{network_id}"
                    )
                )
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)

        if not cex_connectors and not dex_networks:
            help_text = (
                r"🔄 *Select Connector*" + "\n\n" + r"_No connectors available\._"
            )
        else:
            help_text = r"🔄 *Select Connector*"

    except Exception as e:
        logger.error(f"Error fetching connectors: {e}", exc_info=True)
        help_text = r"🔄 *Select Connector*" + "\n\n" + r"_Could not fetch connectors_"

    keyboard.append([InlineKeyboardButton(text="« Back", callback_data="cex:trade")])
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await update.callback_query.message.edit_text(
        help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_trade_connector_select(
    update: Update, context: Context, connector_name: str
) -> None:
    """Handle connector selection"""
    params = context.user_data.get("trade_params", {})
    params["connector"] = connector_name

    # Save unified preference for /trade command
    set_last_trade_connector(context.user_data, "cex", connector_name)

    _invalidate_trade_cache(context.user_data)
    invalidate_cache(context.user_data, "balances", "positions", "trading_rules")

    # Invalidate SDS cache and update subscription for new connector
    user_id = context.user_data.get("_user_id")
    from condor.preferences import get_active_server

    server = get_active_server(context.user_data)
    if server:
        sds = get_server_data_service()
        sds.invalidate(
            server,
            ServerDataType.PORTFOLIO,
            ServerDataType.POSITIONS,
            ServerDataType.TRADING_RULES,
            ServerDataType.PRICES,
        )
        if user_id:
            sub_id = f"tg_trade_{user_id}"
            asyncio.ensure_future(
                sds.subscribe(
                    server,
                    ServerDataType.PRICES,
                    sub_id,
                    interval=3,
                    connector_name=connector_name,
                    trading_pair=params.get("trading_pair", ""),
                )
            )
            asyncio.ensure_future(
                sds.subscribe(
                    server, ServerDataType.ACTIVE_ORDERS, sub_id, interval=10, limit="5"
                )
            )
            asyncio.ensure_future(
                sds.subscribe(
                    server,
                    ServerDataType.TRADING_RULES,
                    sub_id,
                    interval=300,
                    connector_name=connector_name,
                )
            )

    context.user_data["cex_state"] = "trade"

    await show_trade_menu(update, context)


async def handle_trade_set_pair(update: Update, context: Context) -> None:
    """Prompt for trading pair input - edits message"""
    params = context.user_data.get("trade_params", {})
    help_text = (
        r"💱 *Set Trading Pair*" + "\n\n"
        r"Enter the trading pair:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`BTC\-USDT`" + "\n"
        r"`ETH\-USDT`" + "\n"
        r"`SOL\-USDT`"
    )

    keyboard = [[InlineKeyboardButton(text="« Back", callback_data="cex:trade")]]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    context.user_data["cex_state"] = "trade_set_pair"

    await update.callback_query.message.edit_text(
        help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_trade_set_amount(update: Update, context: Context) -> None:
    """Prompt for amount input - edits message"""
    help_text = (
        r"💰 *Set Amount*" + "\n\n"
        r"Enter amount:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`0\.5` \- Trade 0\.5 of base token" + "\n"
        r"`$100` \- Trade $100 worth"
    )

    keyboard = [[InlineKeyboardButton(text="« Back", callback_data="cex:trade")]]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    context.user_data["cex_state"] = "trade_set_amount"

    await update.callback_query.message.edit_text(
        help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_trade_set_price(update: Update, context: Context) -> None:
    """Prompt for price input - edits message"""
    help_text = (
        r"💵 *Set Price*" + "\n\n"
        r"Enter limit price:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`95000`" + "\n"
        r"`88000\.50`"
    )

    keyboard = [[InlineKeyboardButton(text="« Back", callback_data="cex:trade")]]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    context.user_data["cex_state"] = "trade_set_price"

    await update.callback_query.message.edit_text(
        help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_trade_set_leverage(update: Update, context: Context) -> None:
    """Prompt for leverage input - edits message"""
    params = context.user_data.get("trade_params", {})
    connector = params.get("connector", "binance_perpetual")
    trading_pair = params.get("trading_pair", "BTC-USDT")

    current_lev = context.user_data.get(
        _get_leverage_cache_key(connector, trading_pair), DEFAULT_LEVERAGE
    )

    help_text = (
        r"⚡ *Set Leverage*" + "\n\n"
        f"Current: `{current_lev}x` for `{escape_markdown_v2(trading_pair)}`\n\n"
        r"Enter new leverage:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`5` \- Set 5x leverage" + "\n"
        r"`10` \- Set 10x leverage" + "\n"
        r"`20` \- Set 20x leverage"
    )

    keyboard = [[InlineKeyboardButton(text="« Back", callback_data="cex:trade")]]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    context.user_data["cex_state"] = "trade_set_leverage"

    await update.callback_query.message.edit_text(
        help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_trade_toggle_pos_mode(update: Update, context: Context) -> None:
    """Toggle position mode between HEDGE and ONEWAY directly"""
    params = context.user_data.get("trade_params", {})
    connector = params.get("connector", "binance_perpetual")
    account = get_clob_account(context.user_data)

    try:
        chat_id = update.effective_chat.id
        client = await get_client(chat_id, user_data=context.user_data)

        # Get current mode
        current_mode = context.user_data.get(
            _get_position_mode_cache_key(connector), "HEDGE"
        )
        new_mode = "ONEWAY" if current_mode == "HEDGE" else "HEDGE"

        # Set new mode on exchange
        await client.trading.set_position_mode(
            account_name=account, connector_name=connector, position_mode=new_mode
        )

        # Update cache
        context.user_data[_get_position_mode_cache_key(connector)] = new_mode

        # Show updated menu
        await show_trade_menu(update, context, auto_fetch=False)

    except Exception as e:
        logger.error(f"Error toggling position mode: {e}", exc_info=True)
        error_message = format_error_message(f"Failed: {str(e)}")
        await update.callback_query.message.edit_text(
            error_message, parse_mode="MarkdownV2"
        )
