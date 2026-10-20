"""Free-text input processors for trade parameters."""

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
from utils.telegram_formatters import (
    escape_markdown_v2,
    format_error_message,
)

from .._shared import (
    get_cached,
    get_correct_pair_format,
    get_trading_rules,
    invalidate_cache,
    validate_trading_pair,
)
from .helpers import (
    DEFAULT_LEVERAGE,
    _get_leverage_cache_key,
    _get_position_mode_cache_key,
    _is_perpetual_connector,
)
from .menu import _build_trade_keyboard, _build_trade_menu_text, show_trade_menu
from .params import _invalidate_trade_cache

logger = logging.getLogger(__name__)


async def _update_trade_menu_after_input(update: Update, context: Context) -> None:
    """Update the trade menu message after text input"""
    # Delete user's input message
    try:
        await update.message.delete()
    except Exception:
        pass

    # Edit the stored trade menu message
    msg_id = context.user_data.get("trade_menu_message_id")
    chat_id = context.user_data.get("trade_menu_chat_id")

    if msg_id and chat_id:
        params = context.user_data.get("trade_params", {})
        connector = params.get("connector", "binance_perpetual")
        trading_pair = params.get("trading_pair", "BTC-USDT")
        account = get_clob_account(context.user_data)
        is_perpetual = _is_perpetual_connector(connector)

        # Get cached data from SDS (server-scoped), fall back to old cache
        from condor.preferences import get_active_server

        input_server = get_active_server(context.user_data)
        sds = get_server_data_service()

        if input_server:
            balances = sds.get(
                input_server, ServerDataType.PORTFOLIO, account_name=account
            )
            positions = (
                sds.get(
                    input_server, ServerDataType.POSITIONS, connector_name=connector
                )
                if is_perpetual
                else None
            )
            orders = sds.get(input_server, ServerDataType.ACTIVE_ORDERS, limit="5")
            trading_rules = sds.get(
                input_server, ServerDataType.TRADING_RULES, connector_name=connector
            )
            current_price = sds.get(
                input_server,
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

        leverage = None
        position_mode = None
        if is_perpetual:
            leverage = context.user_data.get(
                _get_leverage_cache_key(connector, trading_pair), DEFAULT_LEVERAGE
            )
            position_mode = context.user_data.get(
                _get_position_mode_cache_key(connector), "HEDGE"
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
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=help_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.debug(f"Could not update trade menu: {e}")
            # Fallback: send new message
            await show_trade_menu(update, context, send_new=True)
    else:
        await show_trade_menu(update, context, send_new=True)


async def process_trade(update: Update, context: Context, user_input: str) -> None:
    """Process trade from text input: pair side amount [type] [price] [position]

    This is a QUICK TRADE - it executes immediately, not just updates params.
    """
    try:
        parts = user_input.split()

        if len(parts) < 3:
            raise ValueError("Need: pair side amount [type] [price] [pos]")

        # Get current connector from params
        params = context.user_data.get("trade_params", {})
        connector = params.get("connector", "binance_perpetual")
        account = get_clob_account(context.user_data)

        trading_pair = parts[0].upper()
        side = parts[1].upper()
        amount = parts[2]
        order_type = parts[3].upper() if len(parts) > 3 else "MARKET"
        price = parts[4] if len(parts) > 4 else None
        position_action = parts[5].upper() if len(parts) > 5 else "OPEN"

        # Validate
        if side not in ("BUY", "SELL"):
            raise ValueError(f"Invalid side: {side}. Use BUY or SELL")
        if order_type not in ("MARKET", "LIMIT", "LIMIT_MAKER"):
            raise ValueError(
                f"Invalid type: {order_type}. Use MARKET, LIMIT, or LIMIT_MAKER"
            )
        if order_type in ("LIMIT", "LIMIT_MAKER") and not price:
            raise ValueError("Price required for LIMIT orders")

        # Delete user's input message
        try:
            await update.message.delete()
        except Exception:
            pass

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

        # Execute the trade
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
        # Also invalidate SDS (server-scoped)
        from condor.preferences import get_active_server

        qt_server = get_active_server(context.user_data)
        if qt_server:
            get_server_data_service().invalidate(
                qt_server,
                ServerDataType.PORTFOLIO,
                ServerDataType.ACTIVE_ORDERS,
                ServerDataType.POSITIONS,
            )

        # Update params for next trade
        context.user_data["trade_params"] = {
            "connector": connector,
            "trading_pair": trading_pair,
            "side": side,
            "amount": amount,
            "order_type": order_type,
            "price": price if price else "—",
            "position_mode": position_action,
        }

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

        # Build success message
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

        # Update the trade menu message with success
        msg_id = context.user_data.get("trade_menu_message_id")
        menu_chat_id = context.user_data.get("trade_menu_chat_id")

        if msg_id and menu_chat_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=menu_chat_id,
                    message_id=msg_id,
                    text=order_info,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup,
                )
            except Exception as e:
                logger.debug(f"Could not update trade menu: {e}")
                await update.effective_chat.send_message(
                    order_info, parse_mode="MarkdownV2", reply_markup=reply_markup
                )
        else:
            await update.effective_chat.send_message(
                order_info, parse_mode="MarkdownV2", reply_markup=reply_markup
            )

        context.user_data["cex_state"] = "trade"

    except Exception as e:
        logger.error(f"Error processing quick trade: {e}", exc_info=True)
        error_message = format_error_message(f"Trade failed: {str(e)}")
        await update.message.answer(error_message, parse_mode="MarkdownV2")


async def process_trade_set_pair(
    update: Update, context: Context, user_input: str
) -> None:
    """Process trading pair input with validation against available markets"""
    try:
        params = context.user_data.get("trade_params", {})
        connector = params.get("connector", "binance_perpetual")
        pair_input = user_input.strip().upper().replace("_", "-").replace("/", "-")

        chat_id = update.effective_chat.id
        client = await get_client(chat_id, user_data=context.user_data)

        # Validate trading pair exists on the connector
        is_valid, error_msg, suggestions, correct_pair = await validate_trading_pair(
            context.user_data, client, connector, pair_input
        )

        if is_valid:
            # Use the correct pair format returned by validation
            if correct_pair:
                params["trading_pair"] = correct_pair
            else:
                # Fallback: Get correctly formatted pair from trading rules
                trading_rules = await get_trading_rules(
                    context.user_data, client, connector
                )
                fallback_pair = get_correct_pair_format(trading_rules, pair_input)
                params["trading_pair"] = fallback_pair if fallback_pair else pair_input

            _invalidate_trade_cache(context.user_data)
            context.user_data["cex_state"] = "trade"

            await _update_trade_menu_after_input(update, context)
        else:
            # Show error with suggestions
            await _show_pair_suggestions(
                update, context, pair_input, error_msg, suggestions
            )

    except Exception as e:
        logger.error(f"Error processing trading pair: {e}", exc_info=True)
        error_message = format_error_message(f"Failed: {str(e)}")
        await update.message.answer(error_message, parse_mode="MarkdownV2")


async def _show_pair_suggestions(
    update: Update,
    context: Context,
    input_pair: str,
    error_msg: str,
    suggestions: list,
) -> None:
    """Show trading pair suggestions when validation fails"""
    # Delete user's input message
    try:
        await update.message.delete()
    except Exception:
        pass

    # Build suggestion message
    help_text = f"❌ *{escape_markdown_v2(error_msg)}*\n\n"

    if suggestions:
        help_text += "💡 *Did you mean:*\n"
    else:
        help_text += "_No similar pairs found\\._\n"

    # Build keyboard with suggestions
    keyboard = []
    for pair in suggestions:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"📈 {pair}", callback_data=f"cex:trade_pair_select_{pair}"
                )
            ]
        )

    keyboard.append([InlineKeyboardButton(text="« Back", callback_data="cex:trade")])
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    # Update the stored trade menu message
    msg_id = context.user_data.get("trade_menu_message_id")
    menu_chat_id = context.user_data.get("trade_menu_chat_id")

    if msg_id and menu_chat_id:
        try:
            await context.bot.edit_message_text(
                chat_id=menu_chat_id,
                message_id=msg_id,
                text=help_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.debug(f"Could not update trade menu: {e}")
            await update.effective_chat.send_message(
                help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
    else:
        await update.effective_chat.send_message(
            help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def handle_trade_pair_select(
    update: Update, context: Context, trading_pair: str
) -> None:
    """Handle selection of a suggested trading pair"""
    params = context.user_data.get("trade_params", {})
    params["trading_pair"] = trading_pair

    _invalidate_trade_cache(context.user_data)
    context.user_data["cex_state"] = "trade"

    await show_trade_menu(update, context)


async def process_trade_set_amount(
    update: Update, context: Context, user_input: str
) -> None:
    """Process amount input"""
    try:
        params = context.user_data.get("trade_params", {})
        params["amount"] = user_input.strip()

        _invalidate_trade_cache(context.user_data)
        context.user_data["cex_state"] = "trade"

        await _update_trade_menu_after_input(update, context)

    except Exception as e:
        error_message = format_error_message(f"Failed: {str(e)}")
        await update.message.answer(error_message, parse_mode="MarkdownV2")


async def process_trade_set_price(
    update: Update, context: Context, user_input: str
) -> None:
    """Process price input"""
    try:
        params = context.user_data.get("trade_params", {})
        params["price"] = user_input.strip()

        context.user_data["cex_state"] = "trade"

        await _update_trade_menu_after_input(update, context)

    except Exception as e:
        error_message = format_error_message(f"Failed: {str(e)}")
        await update.message.answer(error_message, parse_mode="MarkdownV2")


async def process_trade_set_leverage(
    update: Update, context: Context, user_input: str
) -> None:
    """Process leverage input - just a number"""
    try:
        leverage = int(user_input.strip().replace("x", "").replace("X", ""))

        if leverage <= 0:
            raise ValueError("Leverage must be positive")

        params = context.user_data.get("trade_params", {})
        connector = params.get("connector", "binance_perpetual")
        trading_pair = params.get("trading_pair", "BTC-USDT")
        account = get_clob_account(context.user_data)

        chat_id = update.effective_chat.id
        client = await get_client(chat_id, user_data=context.user_data)

        # Set leverage on exchange
        await client.trading.set_leverage(
            account_name=account,
            connector_name=connector,
            trading_pair=trading_pair,
            leverage=leverage,
        )

        # Update cache
        context.user_data[_get_leverage_cache_key(connector, trading_pair)] = leverage
        context.user_data["cex_state"] = "trade"

        await _update_trade_menu_after_input(update, context)

    except ValueError as e:
        error_message = format_error_message(str(e))
        await update.message.answer(error_message, parse_mode="MarkdownV2")
    except Exception as e:
        error_message = format_error_message(f"Failed: {str(e)}")
        await update.message.answer(error_message, parse_mode="MarkdownV2")
