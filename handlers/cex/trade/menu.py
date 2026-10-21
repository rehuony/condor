"""Trade menu rendering, refresh and background data loading."""

import asyncio
import logging

from condor.fetchers.market_data import fetch_current_price
from condor.preferences import (
    get_clob_account,
    get_clob_order_defaults,
)
from condor.server_data_service import ServerDataType, get_server_data_service
from condor.telegram.errors import is_not_modified
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
    format_compact_number,
)

from .._shared import (
    get_cached,
    get_cex_balances,
    get_positions,
    get_trading_rules,
    invalidate_cache,
    set_cached,
)
from .helpers import (
    DEFAULT_LEVERAGE,
    _fetch_recent_orders,
    _format_compact_order_line,
    _format_compact_position_line,
    _format_price,
    _get_leverage_cache_key,
    _get_position_mode_cache_key,
    _is_perpetual_connector,
)

logger = logging.getLogger(__name__)


async def handle_trade(update: Update, context: Context) -> None:
    """Handle trade - unified menu for place order and view"""
    if "trade_params" not in context.user_data:
        defaults = get_clob_order_defaults(context.user_data)
        context.user_data["trade_params"] = defaults

    context.user_data["cex_state"] = "trade"

    # Subscribe to SDS data types for active trading refresh
    params = context.user_data.get("trade_params", {})
    user_id = context.user_data.get("_user_id")
    if user_id:
        from condor.preferences import get_active_server

        server = get_active_server(context.user_data)
        if server:
            sds = get_server_data_service()
            sub_id = f"tg_trade_{user_id}"
            connector = params.get("connector", "")
            pair = params.get("trading_pair", "")
            account = get_clob_account(context.user_data)
            # Subscribe to relevant data types for active trading
            import asyncio

            asyncio.ensure_future(
                sds.subscribe(
                    server,
                    ServerDataType.PRICES,
                    sub_id,
                    interval=3,
                    connector_name=connector,
                    trading_pair=pair,
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
                    connector_name=connector,
                )
            )
            if "perpetual" in (connector or "").lower():
                asyncio.ensure_future(
                    sds.subscribe(
                        server,
                        ServerDataType.POSITIONS,
                        sub_id,
                        interval=10,
                        connector_name=connector,
                    )
                )

    await show_trade_menu(update, context)


async def handle_trade_refresh(update: Update, context: Context) -> None:
    """Handle trade refresh - clear cache and reload"""
    query = update.callback_query
    if query:
        await query.answer("Refreshing...")

    # Invalidate both old cache and SDS
    invalidate_cache(context.user_data, "balances", "orders", "positions")
    from condor.preferences import get_active_server

    server = get_active_server(context.user_data)
    if server:
        sds = get_server_data_service()
        sds.invalidate(
            server,
            ServerDataType.PORTFOLIO,
            ServerDataType.ACTIVE_ORDERS,
            ServerDataType.POSITIONS,
            ServerDataType.PRICES,
        )

    await handle_trade(update, context)


def _build_trade_keyboard(
    params: dict,
    is_perpetual: bool = False,
    leverage: int = None,
    position_mode: str = None,
) -> list:
    """Build the trade menu keyboard

    Layout:
    - Row 1: connector
    - Row 2: trading pair | side | order type
    - Row 3: amount | position action
    - Row 4: price (only for LIMIT)
    - Row 5 (perpetual): leverage | pos mode | execute
    - Row 5 (spot): quote | execute
    - Row 6: orders | positions | close
    """
    connector = params.get("connector", "binance_perpetual")

    keyboard = [
        # Row 1: Connector (full width)
        [
            InlineKeyboardButton(
                text=f"🔌 {connector}", callback_data="cex:trade_set_connector"
            )
        ],
        # Row 2: Trading Pair | Side | Order Type
        [
            InlineKeyboardButton(
                text=f"💱 {params.get('trading_pair', 'BTC-USDT')}",
                callback_data="cex:trade_set_pair",
            ),
            InlineKeyboardButton(
                text=f"📈 {params.get('side', 'BUY')}",
                callback_data="cex:trade_toggle_side",
            ),
            InlineKeyboardButton(
                text=f"📋 {params.get('order_type', 'MARKET')}",
                callback_data="cex:trade_toggle_type",
            ),
        ],
    ]

    # Row 3: Amount | Position Action (perpetual only) | Price (LIMIT only)
    row3 = [
        InlineKeyboardButton(
            text=f"💰 {params.get('amount', '0.5')}",
            callback_data="cex:trade_set_amount",
        ),
    ]
    # Only show position action for perpetual exchanges
    if is_perpetual:
        row3.append(
            InlineKeyboardButton(
                text=f"🎯 {params.get('position_mode', 'OPEN')}",
                callback_data="cex:trade_toggle_position",
            )
        )
    if params.get("order_type") in ["LIMIT", "LIMIT_MAKER"]:
        row3.append(
            InlineKeyboardButton(
                text=f"💵 {params.get('price', '—')}",
                callback_data="cex:trade_set_price",
            )
        )
    keyboard.append(row3)

    # Row 5 (perpetual): Leverage | Pos Mode
    # Row 6 (perpetual): Quote | Execute
    if is_perpetual:
        lev_display = f"{leverage}x" if leverage else f"{DEFAULT_LEVERAGE}x"
        mode_display = position_mode or "HEDGE"
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"⚡ {lev_display}", callback_data="cex:trade_set_leverage"
                ),
                InlineKeyboardButton(
                    text=f"🔀 {mode_display}", callback_data="cex:trade_toggle_pos_mode"
                ),
            ]
        )
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="📊 Quote", callback_data="cex:trade_get_quote"
                ),
                InlineKeyboardButton(
                    text="✅ Execute", callback_data="cex:trade_execute"
                ),
            ]
        )
    else:
        # Row 5 (spot): Quote | Execute
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="📊 Quote", callback_data="cex:trade_get_quote"
                ),
                InlineKeyboardButton(
                    text="✅ Execute", callback_data="cex:trade_execute"
                ),
            ]
        )

    keyboard.append([ui.action_button("quick", "Quick Trade", "cex:trade_quick")])

    # Row 6: Navigation
    keyboard.append(
        [
            InlineKeyboardButton(text="📋 Orders", callback_data="cex:search_orders"),
            InlineKeyboardButton(text="📊 Positions", callback_data="cex:positions"),
            InlineKeyboardButton(text="✕ Close", callback_data="cex:close"),
        ]
    )

    return keyboard


def _build_trade_menu_text(
    user_data: dict,
    params: dict,
    balances: dict = None,
    positions: list = None,
    orders: list = None,
    trading_rules: dict = None,
    current_price: float = None,
    quote_data: dict = None,
    server_name: str = None,
    server_status: str = "online",
) -> str:
    """Build the trade menu text content (swap.py style)"""
    connector = params.get("connector", "binance_perpetual")
    trading_pair = params.get("trading_pair", "BTC-USDT")

    # Parse trading pair
    if "-" in trading_pair:
        base_token, quote_token = trading_pair.split("-", 1)
    else:
        base_token, quote_token = trading_pair, "USDT"

    # Build header with server indicator
    if server_name:
        status_emoji = {
            "online": "🟢",
            "offline": "🔴",
            "auth_error": "🟠",
            "error": "⚠️",
        }.get(server_status, "🟢")
        help_text = f"📝 *Trade*\n_Server: {escape_markdown_v2(server_name)} {status_emoji}_\n\n"
    else:
        help_text = r"📝 *Trade*" + "\n\n"

    # Show balances section (with loading placeholder)
    help_text += r"*Balance*" + "\n"
    if balances is not None:
        connector_balances = balances.get(connector, [])
        balances_found = {}

        # Collect base and quote token balances
        for bal in connector_balances:
            token = bal.get("token", "").upper()
            units = float(bal.get("units", 0))
            value = float(bal.get("value", 0))
            if token == base_token.upper() and (units > 0 or value > 0):
                balances_found["base"] = {
                    "units": units,
                    "value": value,
                    "token": token,
                }
            elif token == quote_token.upper() and (units > 0 or value > 0):
                balances_found["quote"] = {
                    "units": units,
                    "value": value,
                    "token": token,
                }

        if balances_found:
            if "base" in balances_found:
                b = balances_found["base"]
                val_str = (
                    f"(${format_compact_number(b['value'])})" if b["value"] > 0 else ""
                )
                help_text += f"💰 `{escape_markdown_v2(b['token'])}`: `{escape_markdown_v2(format_compact_number(b['units']))}` {escape_markdown_v2(val_str)}\n"
            if "quote" in balances_found:
                q = balances_found["quote"]
                val_str = (
                    f"(${format_compact_number(q['value'])})" if q["value"] > 0 else ""
                )
                help_text += f"💵 `{escape_markdown_v2(q['token'])}`: `{escape_markdown_v2(format_compact_number(q['units']))}` {escape_markdown_v2(val_str)}\n"
        else:
            help_text += "_No balance found_\n".replace(".", "\\.")
    else:
        help_text += "⏳ _Loading balances\\.\\.\\._\n"
    help_text += "\n"

    # Show market data section (quote + trading rules combined)
    help_text += f"*{escape_markdown_v2(trading_pair)}*\n"

    # Calculate spread for first line
    spread_str = ""
    buy_price = None
    sell_price = None
    if quote_data and not quote_data.get("loading") and not quote_data.get("error"):
        buy_price = quote_data.get("buy_price")
        sell_price = quote_data.get("sell_price")
        if buy_price and sell_price:
            midpoint = (buy_price + sell_price) / 2
            spread_pct = abs(buy_price - sell_price) / midpoint * 100 if midpoint else 0
            spread_str = f" \\| 📊 Spread: {escape_markdown_v2(f'{spread_pct:.2f}')}%"

    # Line 1: Price | Spread
    if current_price:
        help_text += f"💵 Price: `{escape_markdown_v2(_format_price(current_price))}`{spread_str}\n"
    else:
        help_text += "💵 Price: _loading\\.\\.\\._\n"

    # Line 2: Min amount | Min notional
    if trading_rules and trading_pair in trading_rules:
        rules = trading_rules[trading_pair]
        min_order = rules.get("min_order_size", 0)
        min_notional = rules.get("min_notional_size", 0)
        rules_line = []
        if min_order > 0:
            rules_line.append(f"📏 Min amount: `{format_compact_number(min_order)}`")
        if min_notional > 0:
            rules_line.append(f"Min notional: `${format_compact_number(min_notional)}`")
        if rules_line:
            help_text += (
                escape_markdown_v2(" | ".join(rules_line)).replace("\\`", "`") + "\n"
            )

    # Quote data (buy/sell lines)
    if quote_data:
        if quote_data.get("loading"):
            help_text += "⏳ _Fetching quotes\\.\\.\\._\n"
        elif quote_data.get("error"):
            error_msg = escape_markdown_v2(str(quote_data.get("error")))
            help_text += f"❌ _{error_msg}_\n"
        elif buy_price or sell_price:
            amount_requested = quote_data.get("amount", params.get("amount", "1"))

            try:
                amt_float = float(amount_requested)
            except (ValueError, TypeError):
                amt_float = 0

            if buy_price:
                buy_cost = amt_float * buy_price
                help_text += f"🟢 `BUY  {format_compact_number(amt_float)} {escape_markdown_v2(base_token)} → {format_compact_number(buy_cost, 2)} {escape_markdown_v2(quote_token)} @{format_compact_number(buy_price, 2)}`\n"

            if sell_price:
                sell_proceeds = amt_float * sell_price
                help_text += f"🔴 `SELL {format_compact_number(amt_float)} {escape_markdown_v2(base_token)} → {format_compact_number(sell_proceeds, 2)} {escape_markdown_v2(quote_token)} @{format_compact_number(sell_price, 2)}`\n"
        else:
            help_text += "_No quotes available_\n".replace(".", "\\.")
    else:
        help_text += "⏳ _Loading quotes\\.\\.\\._\n"

    help_text += "\n"

    # Show positions (for perpetual)
    if positions and _is_perpetual_connector(connector):
        conn_positions = [p for p in positions if p.get("connector_name") == connector]
        if conn_positions:
            help_text += r"*Positions*" + "\n"
            for pos in conn_positions[:3]:
                line = _format_compact_position_line(pos)
                help_text += line + "\n"
            if len(conn_positions) > 3:
                help_text += f"_\\+{len(conn_positions) - 3} more_\n"
            help_text += "\n"

    help_text += ui.hint("Tap ⌨️ Quick Trade to type an order in one line.") + "\n\n"

    # Show recent orders (swap.py style)
    if orders:
        help_text += r"*Recent*" + "\n"
        for order in orders[:5]:
            line = _format_compact_order_line(order)
            help_text += line + "\n"

    return help_text


async def show_trade_menu(
    update: Update,
    context: Context,
    send_new: bool = False,
    auto_fetch: bool = True,
    quote_data: dict = None,
) -> None:
    """Display the unified trade menu with balances and data"""
    from config_manager import get_config_manager
    from handlers.bots._shared import resolve_accessible_server_or_none

    params = context.user_data.get("trade_params", {})
    connector = params.get("connector", "binance_perpetual")
    trading_pair = params.get("trading_pair", "BTC-USDT")
    account = get_clob_account(context.user_data)
    is_perpetual = _is_perpetual_connector(connector)

    # Get current server name and status - with proper access control
    cm = get_config_manager()

    # Only use servers the user has access to; the menu renders serverless
    # rather than raising when there is nothing this user may reach.
    server_name = resolve_accessible_server_or_none(context.user_data)
    server_status = "online"
    if server_name:
        try:
            server_status_info = await cm.check_server_status(server_name)
            server_status = server_status_info.get("status", "online")
        except Exception:
            pass

    # Try to get cached data from SDS (server-scoped), fall back to old cache
    sds = get_server_data_service()

    if server_name:
        balances = sds.get(server_name, ServerDataType.PORTFOLIO, account_name=account)
        positions = (
            sds.get(server_name, ServerDataType.POSITIONS, connector_name=connector)
            if is_perpetual
            else None
        )
        orders = sds.get(server_name, ServerDataType.ACTIVE_ORDERS, limit="5")
        trading_rules = sds.get(
            server_name, ServerDataType.TRADING_RULES, connector_name=connector
        )
        current_price = sds.get(
            server_name,
            ServerDataType.PRICES,
            connector_name=connector,
            trading_pair=trading_pair,
        )
    else:
        balances = get_cached(context.user_data, f"cex_balances_{account}", ttl=60)
        positions = (
            get_cached(context.user_data, f"positions_{connector}", ttl=60)
            if is_perpetual
            else None
        )
        orders = get_cached(context.user_data, "recent_orders", ttl=60)
        trading_rules = get_cached(
            context.user_data, f"trading_rules_{connector}", ttl=300
        )
        current_price = context.user_data.get("current_market_price")

    # Use cached quote if available and no explicit quote provided
    if quote_data is None:
        quote_data = get_cached(context.user_data, "trade_quote", ttl=30)

    # Get leverage and position mode for perpetual
    leverage = None
    position_mode = None
    if is_perpetual:
        leverage = context.user_data.get(
            _get_leverage_cache_key(connector, trading_pair), DEFAULT_LEVERAGE
        )
        position_mode = context.user_data.get(
            _get_position_mode_cache_key(connector), "HEDGE"
        )

    # Build text and keyboard
    help_text = _build_trade_menu_text(
        context.user_data,
        params,
        balances,
        positions,
        orders,
        trading_rules,
        current_price,
        quote_data,
        server_name=server_name,
        server_status=server_status,
    )
    keyboard = _build_trade_keyboard(params, is_perpetual, leverage, position_mode)
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    # Send or edit message
    message = None
    if send_new or not update.callback_query:
        if update.message:
            message = await update.message.answer(
                help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
        elif update.callback_query:
            message = await update.callback_query.message.answer(
                help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
    else:
        try:
            await update.callback_query.message.edit_text(
                help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
            message = update.callback_query.message
        except Exception as e:
            if not is_not_modified(e):
                logger.warning(f"Could not edit message: {e}")
            message = update.callback_query.message

    # Store message for later editing
    chat_id = update.effective_chat.id
    if message:
        context.user_data["trade_menu_message_id"] = message.message_id
        context.user_data["trade_menu_chat_id"] = message.chat.id
        ui.remember_menu(context.user_data, "cex_trade", message.message_id)

    # Launch background data fetch if needed (when any key data is missing)
    needs_fetch = balances is None or quote_data is None or current_price is None
    if auto_fetch and message and needs_fetch:
        asyncio.create_task(
            _fetch_trade_data_background(context, message, params, chat_id)
        )


async def _update_trade_message(context: Context, message) -> None:
    """Helper to rebuild and update the trade menu message"""
    if context.user_data.get("cex_state") != "trade":
        return

    params = context.user_data.get("trade_params", {})
    connector = params.get("connector", "binance_perpetual")
    trading_pair = params.get("trading_pair", "BTC-USDT")
    account = get_clob_account(context.user_data)
    is_perpetual = _is_perpetual_connector(connector)

    # Get all cached data from SDS (server-scoped), fall back to old cache
    from condor.preferences import get_active_server

    server = get_active_server(context.user_data)
    sds = get_server_data_service()

    if server:
        balances = sds.get(server, ServerDataType.PORTFOLIO, account_name=account)
        positions = (
            sds.get(server, ServerDataType.POSITIONS, connector_name=connector)
            if is_perpetual
            else None
        )
        orders = sds.get(server, ServerDataType.ACTIVE_ORDERS, limit="5")
        trading_rules = sds.get(
            server, ServerDataType.TRADING_RULES, connector_name=connector
        )
        current_price = sds.get(
            server,
            ServerDataType.PRICES,
            connector_name=connector,
            trading_pair=trading_pair,
        )
    else:
        balances = get_cached(context.user_data, f"cex_balances_{account}", ttl=60)
        positions = (
            get_cached(context.user_data, f"positions_{connector}", ttl=60)
            if is_perpetual
            else None
        )
        orders = get_cached(context.user_data, "recent_orders", ttl=60)
        trading_rules = get_cached(
            context.user_data, f"trading_rules_{connector}", ttl=300
        )
        current_price = context.user_data.get("current_market_price")
    quote_data = get_cached(context.user_data, "trade_quote", ttl=30)

    leverage = (
        context.user_data.get(
            _get_leverage_cache_key(connector, trading_pair), DEFAULT_LEVERAGE
        )
        if is_perpetual
        else None
    )
    position_mode = (
        context.user_data.get(_get_position_mode_cache_key(connector), "HEDGE")
        if is_perpetual
        else None
    )

    help_text = _build_trade_menu_text(
        context.user_data,
        params,
        balances,
        positions,
        orders,
        trading_rules,
        current_price,
        quote_data,
    )
    keyboard = _build_trade_keyboard(params, is_perpetual, leverage, position_mode)
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    try:
        await message.edit_text(
            help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )
    except Exception as e:
        if not is_not_modified(e):
            logger.warning(f"Could not update message: {e}")


async def _fetch_trade_data_background(
    context: Context, message, params: dict, chat_id: int = None
) -> None:
    """Fetch trade data in background and update message when done (like swap.py)"""
    logger.info(f"Starting background fetch for trade data...")
    connector = params.get("connector", "binance_perpetual")
    trading_pair = params.get("trading_pair", "BTC-USDT")
    account = get_clob_account(context.user_data)
    is_perpetual = _is_perpetual_connector(connector)

    try:
        client = await get_client(chat_id, user_data=context.user_data)
    except Exception as e:
        logger.warning(f"Could not get client for trade data: {e}")
        return

    # Get SDS for dual-write (server-scoped)
    from condor.preferences import get_active_server

    server = get_active_server(context.user_data)
    sds = get_server_data_service()

    # Define safe fetch functions
    async def fetch_balances_safe():
        try:
            # Check if force refresh is needed (e.g., after trade execution)
            force_refresh = context.user_data.pop("_force_cex_balance_refresh", False)
            result = await get_cex_balances(
                context.user_data, client, account, refresh=force_refresh
            )
            # Write to SDS
            if server:
                sds.put(server, ServerDataType.PORTFOLIO, result, account_name=account)
            return result
        except Exception as e:
            logger.warning(f"Could not fetch balances: {e}")
            set_cached(context.user_data, f"cex_balances_{account}", {})
            if server:
                sds.put(server, ServerDataType.PORTFOLIO, {}, account_name=account)
            return {}

    async def fetch_price_safe():
        try:
            price = await fetch_current_price(client, connector, trading_pair)
            if price:
                context.user_data["current_market_price"] = price
                if server:
                    sds.put(
                        server,
                        ServerDataType.PRICES,
                        price,
                        connector_name=connector,
                        trading_pair=trading_pair,
                    )
            return price
        except Exception as e:
            logger.warning(f"Could not fetch price: {e}")
            return None

    async def fetch_rules_safe():
        try:
            result = await get_trading_rules(context.user_data, client, connector)
            if server:
                sds.put(
                    server,
                    ServerDataType.TRADING_RULES,
                    result,
                    connector_name=connector,
                )
            return result
        except Exception as e:
            logger.warning(f"Could not fetch rules: {e}")
            return None

    async def fetch_orders_safe():
        try:
            orders = await _fetch_recent_orders(client, limit=5)
            set_cached(context.user_data, "recent_orders", orders)
            if server:
                sds.put(server, ServerDataType.ACTIVE_ORDERS, orders, limit="5")
            return orders
        except Exception as e:
            logger.warning(f"Could not fetch orders: {e}")
            return []

    async def fetch_quote_safe():
        try:
            amount = params.get("amount", "1")
            volume = float(str(amount).replace("$", ""))

            # If amount is in USD, convert to base token volume
            if "$" in str(amount):
                current_price = (
                    await fetch_current_price(
                        client, connector, trading_pair, strict=True
                    )
                    or 1
                )
                volume = volume / current_price

            # Fetch BUY and SELL quotes
            async def get_quote(is_buy: bool):
                try:
                    result = await client.market_data.get_price_for_volume(
                        connector_name=connector,
                        trading_pair=trading_pair,
                        volume=volume,
                        is_buy=is_buy,
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
                except Exception:
                    return None

            buy_price, sell_price = await asyncio.gather(
                get_quote(True), get_quote(False)
            )

            # Fallback to mid price if quotes failed
            if buy_price is None and sell_price is None:
                try:
                    mid_price = await fetch_current_price(
                        client, connector, trading_pair
                    )
                    if mid_price:
                        buy_price = mid_price * 1.0005
                        sell_price = mid_price * 0.9995
                except Exception:
                    pass

            quote_data = {
                "trading_pair": trading_pair,
                "amount": volume,
                "buy_price": buy_price,
                "sell_price": sell_price,
            }
            set_cached(context.user_data, "trade_quote", quote_data)
            return quote_data
        except Exception as e:
            logger.warning(f"Could not fetch quote: {e}")
            set_cached(context.user_data, "trade_quote", {"error": str(e)})
            return None

    async def fetch_positions_safe():
        try:
            result = await get_positions(context.user_data, client, connector)
            if server:
                sds.put(
                    server, ServerDataType.POSITIONS, result, connector_name=connector
                )
            return result
        except Exception as e:
            logger.warning(f"Could not fetch positions: {e}")
            return []

    async def fetch_perpetual_settings_safe():
        try:
            mode_result = await client.trading.get_position_mode(
                account_name=account, connector_name=connector
            )
            position_mode = mode_result.get("position_mode", "HEDGE")
            context.user_data[_get_position_mode_cache_key(connector)] = position_mode

            lev_key = _get_leverage_cache_key(connector, trading_pair)
            if lev_key not in context.user_data:
                try:
                    await client.trading.set_leverage(
                        account_name=account,
                        connector_name=connector,
                        trading_pair=trading_pair,
                        leverage=DEFAULT_LEVERAGE,
                    )
                    context.user_data[lev_key] = DEFAULT_LEVERAGE
                except Exception:
                    pass
            return position_mode
        except Exception as e:
            logger.warning(f"Could not fetch perpetual settings: {e}")
            return None

    # Fetch all data in parallel
    tasks = [
        fetch_balances_safe(),
        fetch_price_safe(),
        fetch_rules_safe(),
        fetch_orders_safe(),
        fetch_quote_safe(),
    ]

    if is_perpetual:
        tasks.extend(
            [
                fetch_positions_safe(),
                fetch_perpetual_settings_safe(),
            ]
        )

    # Wait for all to complete
    await asyncio.gather(*tasks, return_exceptions=True)

    # Update message once after all data is fetched
    logger.info(f"Background fetch complete, updating message...")
    await _update_trade_message(context, message)
