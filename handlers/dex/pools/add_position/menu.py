"""Add-position menu and its refresh/toggle actions."""

import logging

from condor.pool_data import (
    fetch_ohlcv,
    get_gecko_network,
)
from condor.preferences import get_dex_last_pool
from condor.telegram.errors import is_not_modified
from condor.telegram.transport import as_input_file
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
    resolve_token_symbol,
)

from ..._shared import (
    DEFAULT_CACHE_TTL,
    get_cached,
    set_cached,
)
from ...visualizations import (
    generate_combined_chart,
    generate_liquidity_chart,
)
from .._common import (
    _fetch_pool_info,
    _fetch_token_balances,
    _require_network,
    get_token_cache_from_gateway,
)
from ..add_position.range_math import (
    _calculate_max_range,
    _generate_range_ascii,
    _get_default_range_percent,
    _price_to_percent,
    _validate_bin_range,
)

logger = logging.getLogger(__name__)


async def handle_add_position(update: Update, context: Context) -> None:
    """Handle add CLMM position"""

    # Initialize position params with defaults or last used
    if "add_position_params" not in context.user_data:
        last_pool = get_dex_last_pool(context.user_data)
        context.user_data["add_position_params"] = {
            "connector": last_pool.get("connector", "meteora"),
            "network": last_pool.get("network", "solana-mainnet-beta"),
            "pool_address": last_pool.get("pool_address", ""),
            "lower_price": "",
            "upper_price": "",
            "amount_base": "10%",  # Default to 10% of balance
            "amount_quote": "10%",  # Default to 10% of balance
            "strategy_type": "0",  # Default strategy type (Spot for Meteora)
        }

    context.user_data["dex_state"] = "add_position"

    await show_add_position_menu(update, context)


async def show_add_position_menu(
    update: Update,
    context: Context,
    send_new: bool = False,
    show_help: bool = False,
    timeframe: str = "1h",
) -> None:
    """Display the add position configuration menu with liquidity chart

    Args:
        update: The update object
        context: The context object
        send_new: If True, always send a new message instead of editing
        show_help: If True, show detailed help instead of balances/ASCII
        timeframe: OHLCV timeframe (1m, 1h, 1d) - default 1h
    """
    from io import BytesIO

    # The menu is navigation; Quick Edit arms typed input explicitly
    context.user_data["dex_state"] = "add_position"

    params = context.user_data.get("add_position_params", {})

    # Get pool info if available for range suggestions
    selected_pool = context.user_data.get("selected_pool", {})
    pool_info = context.user_data.get("selected_pool_info", {})

    # If we have a pool but no pool_info with bins, try to use cached or fetch
    pool_address = params.get("pool_address") or selected_pool.get(
        "pool_address", selected_pool.get("address", "")
    )
    connector = params.get("connector") or selected_pool.get("connector", "meteora")

    if pool_address and not pool_info.get("bins"):
        # Try to get from cache first
        cache_key = f"pool_info_{connector}_{pool_address}"
        cached_info = get_cached(context.user_data, cache_key, ttl=DEFAULT_CACHE_TTL)
        if cached_info and cached_info.get("bins"):
            pool_info = cached_info
            context.user_data["selected_pool_info"] = pool_info

    # Get current price and bin step for range calculation
    # Prefer pool_info (fetched data) over selected_pool (list data)
    current_price = pool_info.get("price") or selected_pool.get("current_price")
    bin_step = pool_info.get("bin_step") or selected_pool.get("bin_step")
    bins = pool_info.get("bins", [])
    network = params.get("network") or _require_network(selected_pool, "This pool")

    # Extract token addresses. pool-info names them base_token_address /
    # quote_token_address; the pool list names them mint_x / mint_y.
    base_token = selected_pool.get("base_token")
    quote_token = selected_pool.get("quote_token")

    if not base_token:
        base_token = selected_pool.get("mint_x") or pool_info.get("base_token_address")
    if not quote_token:
        quote_token = selected_pool.get("mint_y") or pool_info.get(
            "quote_token_address"
        )

    # Pair name is a pool-LIST field; pool-info has none.
    pair = selected_pool.get("trading_pair")
    if not pair or pair in ("Pool", "N/A", "Unknown Pair"):
        # Try to resolve from token addresses using token_cache
        if base_token and quote_token:
            token_cache = context.user_data.get("token_cache")
            if not token_cache:
                chat_id = update.effective_chat.id
                token_cache = await get_token_cache_from_gateway(
                    chat_id=chat_id, context=context
                )
                context.user_data["token_cache"] = token_cache

            base_symbol = resolve_token_symbol(base_token, token_cache)
            quote_symbol = resolve_token_symbol(quote_token, token_cache)
            pair = f"{base_symbol}-{quote_symbol}"
        else:
            pair = "Pool"

    # Extract base/quote symbols - prioritize trading pair over address resolution
    if pair and pair not in ("Pool", "N/A", "Unknown Pair"):
        if "-" in pair:
            parts = pair.split("-")
            base_symbol = parts[0].strip()
            quote_symbol = parts[1].strip() if len(parts) > 1 else "QUOTE"
        elif "/" in pair:
            parts = pair.split("/")
            base_symbol = parts[0].strip()
            quote_symbol = parts[1].strip() if len(parts) > 1 else "QUOTE"
        else:
            base_symbol = pair
            quote_symbol = "QUOTE"
    else:
        # Fallback to resolving from addresses (may return truncated)
        token_cache = context.user_data.get("token_cache", {})
        base_symbol = (
            resolve_token_symbol(base_token, token_cache) if base_token else "BASE"
        )
        quote_symbol = (
            resolve_token_symbol(quote_token, token_cache) if quote_token else "QUOTE"
        )

    # Calculate max range (20 bins each side = 41 total) and auto-fill if not set
    suggested_lower, suggested_upper = None, None
    default_lower_pct, default_upper_pct = None, None
    if current_price and bin_step:
        try:
            current_price_float = float(current_price)
            bin_step_int = int(bin_step)

            # Get default percentages for 20 bins each side
            default_lower_pct, default_upper_pct = _get_default_range_percent(
                bin_step_int, 20
            )

            suggested_lower, suggested_upper = _calculate_max_range(
                current_price_float, bin_step_int
            )
            # Auto-fill if empty - store both price and percentage
            if suggested_lower and not params.get("lower_price"):
                params["lower_price"] = f"{suggested_lower:.10f}".rstrip("0").rstrip(
                    "."
                )
                params["lower_pct"] = default_lower_pct
            if suggested_upper and not params.get("upper_price"):
                params["upper_price"] = f"{suggested_upper:.10f}".rstrip("0").rstrip(
                    "."
                )
                params["upper_pct"] = default_upper_pct

            # Calculate percentages for existing prices if not set
            if params.get("lower_price") and not params.get("lower_pct"):
                try:
                    params["lower_pct"] = _price_to_percent(
                        current_price_float, float(params["lower_price"])
                    )
                except (ValueError, TypeError):
                    pass
            if params.get("upper_price") and not params.get("upper_pct"):
                try:
                    params["upper_pct"] = _price_to_percent(
                        current_price_float, float(params["upper_price"])
                    )
                except (ValueError, TypeError):
                    pass

            context.user_data["add_position_params"] = params
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to calculate range: {e}")

    # Get current range values for visualization
    try:
        lower_val = (
            float(params.get("lower_price", 0)) if params.get("lower_price") else None
        )
        upper_val = (
            float(params.get("upper_price", 0)) if params.get("upper_price") else None
        )
        current_val = float(current_price) if current_price else None
    except (ValueError, TypeError):
        lower_val, upper_val, current_val = None, None, None

    # Debug logging for ASCII visualization
    logger.info(
        f"Add position - bins: {len(bins)}, lower: {lower_val}, upper: {upper_val}, price: {current_val}"
    )

    if show_help:
        # ========== HELP VIEW ==========
        help_text = r"📖 *Add Position \- Help*" + "\n\n"

        help_text += r"*🎮 Button Guide*" + "\n"

        help_text += r"• *Row 1:* Lower \\& Upper Price Bounds" + "\n"
        help_text += r"  _Define your position's price range_" + "\n\n"

        help_text += r"• *Row 2:* Base \\& Quote Amounts" + "\n"
        help_text += r"  _Set how much to deposit_" + "\n\n"

        help_text += r"• *Row 3:* Strategy Type" + "\n"
        help_text += r"  _Meteora liquidity distribution_" + "\n\n"

        help_text += r"*💰 Amount Formats*" + "\n"

        help_text += r"• `10%` \- 10% of your wallet balance" + "\n"
        help_text += r"• `100` \- Exact 100 tokens" + "\n"
        help_text += r"• `0\.5` \- Exact 0\.5 tokens" + "\n\n"

        help_text += r"*🎯 Strategy Types*" + "\n"

        help_text += r"• `0` \- *Spot*: Uniform distribution" + "\n"
        help_text += r"• `1` \- *Curve*: Bell curve around price" + "\n"
        help_text += r"• `2` \- *Bid Ask*: Split at current price" + "\n\n"

        help_text += r"*⚡ Quick Edit*" + "\n"

        help_text += r"Type multiple values at once:" + "\n"
        help_text += r"• `l:0\.89 \- u:1\.47`" + "\n"
        help_text += r"• `l:5% \- u:10%` _\(% from price\)_" + "\n"
        help_text += r"• `l:0\.89 \- u:1\.47 \- b:20% \- q:20%`" + "\n\n"

        help_text += r"*Price %:* `l:5%` = \-5% from price" + "\n"
        help_text += r"         `u:10%` = \+10% from price" + "\n"
        help_text += r"         `l:\-3%` `u:\+15%` _explicit signs_" + "\n\n"

        help_text += r"Keys: `l`=lower, `u`=upper, `b`=base, `q`=quote" + "\n\n"

        help_text += r"*📊 Chart Legend*" + "\n"

        help_text += r"• `█` \- Bins in your selected range" + "\n"
        help_text += r"• `░` \- Bins outside your range" + "\n"
        help_text += r"• `◄` \- Current market price" + "\n"
        help_text += r"• `↓` \- Near your lower bound" + "\n"
        help_text += r"• `↑` \- Near your upper bound" + "\n"

    else:
        # ========== MAIN VIEW ==========
        help_text = r"➕ *Add CLMM Position*" + "\n\n"

        # Pool info header - show pair and address
        help_text += f"🏊 *Pool:* `{escape_markdown_v2(pair)}`\n"
        if pool_address:
            help_text += f"📍 *Address:*\n`{escape_markdown_v2(pool_address)}`\n"
        if current_price:
            help_text += (
                f"💱 *Price:* `{escape_markdown_v2(str(current_price)[:10])}`\n"
            )
        if bin_step:
            help_text += f"📊 *Bin Step:* `{escape_markdown_v2(str(bin_step))}` _\\(default 20 bins each side\\)_\n"
        # Add fee if available (pool-info: fee_pct; pool list: base_fee_percentage)
        base_fee = pool_info.get("fee_pct") or selected_pool.get("base_fee_percentage")
        if base_fee:
            help_text += f"💸 *Fee:* `{escape_markdown_v2(str(base_fee))}%`\n"

        # Fetch and display token balances (cached with 60s TTL)
        try:
            balance_cache_key = f"token_balances_{network}_{base_symbol}_{quote_symbol}"
            balances = get_cached(
                context.user_data, balance_cache_key, ttl=DEFAULT_CACHE_TTL
            )
            if balances is None:
                chat_id = update.effective_chat.id
                client = await get_client(chat_id, user_data=context.user_data)
                balances = await _fetch_token_balances(
                    client, network, base_symbol, quote_symbol
                )
                set_cached(context.user_data, balance_cache_key, balances)

            # Always show wallet balances section
            help_text += "\n" + r"*Wallet Balances*" + "\n"

            # Format base token balance - always show, even if 0
            base_bal_str = format_compact_number(balances["base_balance"])
            base_val_str = (
                f"${format_compact_number(balances['base_value'])}"
                if balances["base_value"] > 0
                else ""
            )
            help_text += f"💰 `{escape_markdown_v2(base_symbol)}`: `{escape_markdown_v2(base_bal_str)}` {escape_markdown_v2(base_val_str)}\n"

            # Format quote token balance - always show, even if 0
            quote_bal_str = format_compact_number(balances["quote_balance"])
            quote_val_str = (
                f"${format_compact_number(balances['quote_value'])}"
                if balances["quote_value"] > 0
                else ""
            )
            help_text += f"💵 `{escape_markdown_v2(quote_symbol)}`: `{escape_markdown_v2(quote_bal_str)}` {escape_markdown_v2(quote_val_str)}\n"

            # Store balances in context for percentage calculation
            context.user_data["token_balances"] = balances

        except Exception as e:
            logger.warning(f"Could not fetch token balances: {e}")
            balances = context.user_data.get(
                "token_balances", {"base_balance": 0, "quote_balance": 0}
            )

        # ========== POSITION SUMMARY ==========
        # Show the position that will be created with current parameters
        help_text += "\n" + r"*Position Preview*" + "\n"

        # Calculate actual amounts from percentages
        base_amount_str = params.get("amount_base", "10%")
        quote_amount_str = params.get("amount_quote", "10%")

        try:
            if base_amount_str.endswith("%"):
                base_pct = float(base_amount_str[:-1])
                base_amount = balances.get("base_balance", 0) * base_pct / 100
            else:
                base_amount = float(base_amount_str) if base_amount_str else 0
        except (ValueError, TypeError):
            base_amount = 0

        try:
            if quote_amount_str.endswith("%"):
                quote_pct = float(quote_amount_str[:-1])
                quote_amount = balances.get("quote_balance", 0) * quote_pct / 100
            else:
                quote_amount = float(quote_amount_str) if quote_amount_str else 0
        except (ValueError, TypeError):
            quote_amount = 0

        # Price range with percentages
        lower_pct = params.get("lower_pct")
        upper_pct = params.get("upper_pct")
        lower_price_str = params.get("lower_price", "")
        upper_price_str = params.get("upper_price", "")

        if lower_price_str and upper_price_str:
            try:
                lower_p = float(lower_price_str)
                upper_p = float(upper_price_str)

                # Format prices nicely
                if lower_p >= 1:
                    l_str = f"{lower_p:.4f}"
                else:
                    l_str = f"{lower_p:.6f}"
                if upper_p >= 1:
                    u_str = f"{upper_p:.4f}"
                else:
                    u_str = f"{upper_p:.6f}"

                # Show range with percentages
                l_pct_str = f"({lower_pct:+.1f}%)" if lower_pct is not None else ""
                u_pct_str = f"({upper_pct:+.1f}%)" if upper_pct is not None else ""
                help_text += f"📉 *Lower:* `{escape_markdown_v2(l_str)}` _{escape_markdown_v2(l_pct_str)}_\n"
                help_text += f"📈 *Upper:* `{escape_markdown_v2(u_str)}` _{escape_markdown_v2(u_pct_str)}_\n"

                # Validate bin range
                if current_price and bin_step:
                    is_valid, total_bins, error_msg = _validate_bin_range(
                        lower_p,
                        upper_p,
                        float(current_price),
                        int(bin_step),
                        max_bins=68,
                    )
                    if not is_valid:
                        help_text += f"⚠️ _{escape_markdown_v2(error_msg)}_\n"
                    elif total_bins > 0:
                        help_text += f"📊 *Bins:* `{total_bins}` _\\(max 68\\)_\n"
            except (ValueError, TypeError):
                pass

        # Show amounts with $ values
        base_amt_fmt = escape_markdown_v2(format_compact_number(base_amount))
        quote_amt_fmt = escape_markdown_v2(format_compact_number(quote_amount))

        # Calculate $ values for amounts using actual token prices from wallet
        try:
            base_bal = balances.get("base_balance", 0)
            quote_bal = balances.get("quote_balance", 0)
            base_val = balances.get("base_value", 0)
            quote_val = balances.get("quote_value", 0)
            # Calculate USD price per token from wallet balance (value / units)
            base_price_usd = (base_val / base_bal) if base_bal > 0 else 0
            quote_price_usd = (quote_val / quote_bal) if quote_bal > 0 else 0
            base_usd = base_amount * base_price_usd
            quote_usd = quote_amount * quote_price_usd
        except (ValueError, TypeError, ZeroDivisionError):
            base_usd, quote_usd = 0, 0

        base_usd_str = (
            f" _${escape_markdown_v2(format_compact_number(base_usd))}_"
            if base_usd > 0
            else ""
        )
        quote_usd_str = (
            f" _${escape_markdown_v2(format_compact_number(quote_usd))}_"
            if quote_usd > 0
            else ""
        )

        help_text += f"💰 *Add:* `{base_amt_fmt}` {escape_markdown_v2(base_symbol)}{base_usd_str}\n"
        help_text += f"💵 *Add:* `{quote_amt_fmt}` {escape_markdown_v2(quote_symbol)}{quote_usd_str}\n"

        # Add edit template - each field on its own line
        l_pct_edit = f"{lower_pct:.1f}%" if lower_pct is not None else "-5%"
        # For upper, don't show + prefix (parser treats unsigned as positive)
        u_pct_edit = f"{upper_pct:.1f}%" if upper_pct is not None else "5%"
        help_text += "\n" + ui.hint("Tap ⌨️ Quick Edit, then send:") + "\n"
        help_text += f"`L:{escape_markdown_v2(l_pct_edit)}`\n"
        help_text += f"`U:{escape_markdown_v2(u_pct_edit)}`\n"
        help_text += f"`B:{escape_markdown_v2(base_amount_str)}`\n"
        help_text += f"`Q:{escape_markdown_v2(quote_amount_str)}`\n"

        # NOTE: ASCII visualization is added AFTER we know if chart image is available
        # This is done below, after chart_bytes is generated

    # Build simplified keyboard (no L/U/B/Q buttons - use text input instead)
    strategy_display = params.get("strategy_type", "0")
    strategy_names = {"0": "Spot", "1": "Curve", "2": "BidAsk"}
    strategy_name = strategy_names.get(strategy_display, "Spot")

    # Build GeckoTerminal link for pool
    gecko_url = f"https://www.geckoterminal.com/solana/pools/{pool_address}"

    # Timeframe buttons - highlight current
    tf_1m = "• 1m •" if timeframe == "1m" else "1m"
    tf_1h = "• 1h •" if timeframe == "1h" else "1h"
    tf_1d = "• 1d •" if timeframe == "1d" else "1d"

    keyboard = [
        # Row 1: Timeframe selection
        [
            InlineKeyboardButton(text=tf_1m, callback_data="dex:pos_tf:1m"),
            InlineKeyboardButton(text=tf_1h, callback_data="dex:pos_tf:1h"),
            InlineKeyboardButton(text=tf_1d, callback_data="dex:pos_tf:1d"),
        ],
        # Row 2: Strategy + Add Position
        [
            InlineKeyboardButton(
                text=f"🎯 {strategy_name}", callback_data="dex:pos_toggle_strategy"
            ),
            ui.action_button("quick", "Quick Edit", "dex:pos_quick_edit"),
            InlineKeyboardButton(
                text="➕ Add Position", callback_data="dex:pos_add_confirm"
            ),
        ],
        # Row 3: Refresh + Gecko + Back
        [
            InlineKeyboardButton(text="🔄 Refresh", callback_data="dex:pos_refresh"),
            InlineKeyboardButton(text="🦎 Gecko", url=gecko_url),
            InlineKeyboardButton(
                text="« Back", callback_data="dex:pool_detail_refresh"
            ),
        ],
    ]

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    # Generate combined OHLCV + Liquidity chart (only for main view, not help)
    chart_bytes = None
    if not show_help:
        # Try to get cached OHLCV data for combined chart
        try:
            gecko_network = get_gecko_network(network)
            ohlcv_cache_key = f"ohlcv_{gecko_network}_{pool_address}_{timeframe}_token"
            ohlcv_data = get_cached(
                context.user_data, ohlcv_cache_key, ttl=300
            )  # 5min cache

            if ohlcv_data is None:
                # Fetch OHLCV data
                ohlcv_data, ohlcv_error = await fetch_ohlcv(
                    pool_address=pool_address,
                    network=gecko_network,
                    timeframe=timeframe,
                    currency="token",
                )
                if ohlcv_error:
                    logger.warning(f"OHLCV fetch error: {ohlcv_error}")
                    ohlcv_data = []

            # Generate combined chart if we have OHLCV or bins
            if ohlcv_data or bins:
                chart_bytes = generate_combined_chart(
                    ohlcv_data=ohlcv_data or [],
                    bins=bins or [],
                    pair_name=pair,
                    timeframe=timeframe,
                    current_price=current_val,
                    base_symbol=base_symbol,
                    quote_symbol=quote_symbol,
                    lower_price=lower_val,
                    upper_price=upper_val,
                )
        except Exception as e:
            logger.warning(f"Failed to generate combined chart: {e}")

        # Fallback to liquidity-only chart if combined chart failed
        if not chart_bytes and bins:
            try:
                chart_bytes = generate_liquidity_chart(
                    bins=bins,
                    active_bin_id=pool_info.get("active_bin_id"),
                    current_price=current_val,
                    pair_name=pair,
                    lower_price=lower_val,
                    upper_price=upper_val,
                )
            except Exception as e:
                logger.warning(f"Failed to generate fallback chart: {e}")

    # Add ASCII visualization ONLY if there's NO chart image (ASCII is fallback)
    # This avoids caption too long error on Telegram (1024 char limit for photo captions)
    if not chart_bytes and not show_help and bins and lower_val and upper_val:
        ascii_lines = _generate_range_ascii(bins, lower_val, upper_val, current_val)
        if ascii_lines:
            help_text += "\n```\n" + ascii_lines + "\n```\n"
            help_text += r"_█ in range  ░ out  ◄ current price  ↓↑ your bounds_" + "\n"

    # Determine how to send
    # Check for loading message from timeframe switch
    loading_msg_id = context.user_data.pop("_loading_msg_id", None)
    loading_chat_id = context.user_data.pop("_loading_chat_id", None)

    # Check if we have a stored menu message we can edit
    stored_menu_msg_id = context.user_data.get("add_position_menu_msg_id")
    stored_menu_chat_id = context.user_data.get("add_position_menu_chat_id")
    stored_menu_is_photo = context.user_data.get("add_position_menu_is_photo", False)

    if loading_msg_id or send_new or not update.callback_query:
        # Loading message case, send_new, or text input - need to send new message
        chat = (
            update.callback_query.message.chat
            if update.callback_query
            else update.message.chat
        )

        # Delete loading message if it exists
        if loading_msg_id and loading_chat_id:
            try:
                await context.bot.delete_message(
                    chat_id=loading_chat_id, message_id=loading_msg_id
                )
            except Exception:
                pass

        # For text input updates with chart, delete old message and send new one
        # (can't replace photo, only edit caption)
        if stored_menu_msg_id and stored_menu_chat_id and not loading_msg_id:
            # Delete old message to allow sending new one with updated chart
            try:
                await context.bot.delete_message(
                    chat_id=stored_menu_chat_id, message_id=stored_menu_msg_id
                )
            except Exception:
                pass  # Message may already be deleted

        # Send new message and store its ID
        if chart_bytes:
            try:
                # Handle both BytesIO objects and raw bytes
                if isinstance(chart_bytes, BytesIO):
                    photo_file = chart_bytes
                    photo_file.seek(0)  # Ensure we're at the start
                else:
                    photo_file = BytesIO(chart_bytes)
                photo_file.name = "ohlcv_liquidity.png"
                sent_msg = await chat.send_photo(
                    photo=as_input_file(photo_file),
                    caption=help_text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup,
                )
                context.user_data["add_position_menu_msg_id"] = sent_msg.message_id
                context.user_data["add_position_menu_chat_id"] = chat.id
                context.user_data["add_position_menu_is_photo"] = True
            except Exception as e:
                logger.warning(f"Failed to send chart: {e}")
                sent_msg = await chat.send_message(
                    text=help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
                )
                context.user_data["add_position_menu_msg_id"] = sent_msg.message_id
                context.user_data["add_position_menu_chat_id"] = chat.id
                context.user_data["add_position_menu_is_photo"] = False
        else:
            sent_msg = await chat.send_message(
                text=help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
            context.user_data["add_position_menu_msg_id"] = sent_msg.message_id
            context.user_data["add_position_menu_chat_id"] = chat.id
            context.user_data["add_position_menu_is_photo"] = False
    else:
        # Try to edit caption if it's a photo, otherwise edit text
        # Prioritize editing over delete+resend to avoid message flicker
        msg = update.callback_query.message

        # Store message ID for future text input edits
        context.user_data["add_position_menu_msg_id"] = msg.message_id
        context.user_data["add_position_menu_chat_id"] = msg.chat.id
        context.user_data["add_position_menu_is_photo"] = bool(msg.photo)

        try:
            if msg.photo:
                # It's a photo, edit caption only (keep existing image)
                await msg.edit_caption(
                    caption=help_text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup,
                )
            else:
                await msg.edit_text(
                    text=help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
                )
        except Exception as e:
            if is_not_modified(e):
                pass
            else:
                # Log error but don't delete message - just try updating keyboard
                logger.warning(f"Failed to edit message: {e}")
                try:
                    # Try updating just the keyboard as fallback
                    if msg.photo:
                        await msg.edit_reply_markup(reply_markup=reply_markup)
                    else:
                        await msg.edit_reply_markup(reply_markup=reply_markup)
                except Exception:
                    pass


async def handle_pos_help(update: Update, context: Context) -> None:
    """Show detailed help for add position"""
    await show_add_position_menu(update, context, show_help=True)


async def handle_pos_toggle_strategy(update: Update, context: Context) -> None:
    """Toggle between strategy types (0=Spot, 1=Curve, 2=BidAsk)"""
    params = context.user_data.get("add_position_params", {})
    current_strategy = params.get("strategy_type", "0")

    # Cycle through strategies: 0 -> 1 -> 2 -> 0
    if current_strategy == "0":
        params["strategy_type"] = "1"
    elif current_strategy == "1":
        params["strategy_type"] = "2"
    else:
        params["strategy_type"] = "0"

    await show_add_position_menu(update, context)


async def handle_pos_refresh(
    update: Update, context: Context, timeframe: str = None
) -> None:
    """Refresh pool info and token balances by clearing cache

    Args:
        timeframe: Optional OHLCV timeframe (1m, 1h, 1d). If None, full refresh with default 1h.
    """
    from ..._shared import clear_cache

    query = update.callback_query

    # Get current pool info to build cache keys
    params = context.user_data.get("add_position_params", {})
    selected_pool = context.user_data.get("selected_pool", {})

    pool_address = params.get("pool_address") or selected_pool.get("pool_address", "")
    connector = params.get("connector") or selected_pool.get("connector", "meteora")
    network = params.get("network") or _require_network(selected_pool, "This pool")
    base_token = selected_pool.get("base_token", "")
    quote_token = selected_pool.get("quote_token", "")

    # Only clear cache and refetch on full refresh (no timeframe switch)
    if timeframe is None:
        # Clear specific cache keys
        pool_cache_key = f"pool_info_{connector}_{pool_address}"
        balance_cache_key = f"token_balances_{network}_{base_token}_{quote_token}"

        clear_cache(context.user_data, pool_cache_key)
        clear_cache(context.user_data, balance_cache_key)

        # Also clear stored pool info to force refresh
        context.user_data.pop("selected_pool_info", None)

        # Refetch pool info
        if pool_address:
            chat_id = update.effective_chat.id
            client = await get_client(chat_id, user_data=context.user_data)
            pool_info = await _fetch_pool_info(client, pool_address, connector, network)
            set_cached(context.user_data, pool_cache_key, pool_info)
            context.user_data["selected_pool_info"] = pool_info

        await query.answer("Refreshed!")
        timeframe = "1h"  # Default timeframe
    else:
        # Timeframe switch - show loading transition
        await query.answer(f"Loading {timeframe} candles...")

        # Show loading state - handle both photo and text messages
        pair = selected_pool.get("trading_pair", selected_pool.get("name", "Pool"))
        loading_msg = rf"⏳ *Loading {timeframe} candles\.\.\.*" + "\n\n"
        loading_msg += f"🏊 *Pool:* `{escape_markdown_v2(pair)}`\n"
        loading_msg += r"_Fetching OHLCV data\.\.\._"

        loading_keyboard = [
            [InlineKeyboardButton(text="⏳ Loading…", callback_data="dex:noop")]
        ]

        try:
            # Delete current message (works for both photo and text)
            await query.message.delete()
        except Exception:
            pass

        # Send loading message
        loading_sent = await query.message.answer(
            loading_msg,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=loading_keyboard),
        )
        # Store loading message ID for deletion
        context.user_data["_loading_msg_id"] = loading_sent.message_id
        context.user_data["_loading_chat_id"] = loading_sent.chat.id

    await show_add_position_menu(update, context, timeframe=timeframe)


async def handle_pos_use_max_range(update: Update, context: Context) -> None:
    """Auto-fill the default range (41 bins = 20 each side) based on current price and bin step"""
    selected_pool = context.user_data.get("selected_pool", {})
    pool_info = context.user_data.get("selected_pool_info", {})

    current_price = pool_info.get("price") or selected_pool.get("current_price")
    bin_step = selected_pool.get("bin_step") or pool_info.get("bin_step")

    if not current_price or not bin_step:
        await update.callback_query.answer(
            "No pool info available. Select a pool first."
        )
        return

    try:
        suggested_lower, suggested_upper = _calculate_max_range(
            float(current_price), int(bin_step)
        )

        if suggested_lower and suggested_upper:
            params = context.user_data.get("add_position_params", {})
            params["lower_price"] = f"{suggested_lower:.6f}"
            params["upper_price"] = f"{suggested_upper:.6f}"

            await update.callback_query.answer(
                "Default range (20 bins each side) applied!"
            )
            await show_add_position_menu(update, context)
        else:
            await update.callback_query.answer("Could not calculate range")
    except Exception as e:
        logger.error(f"Error calculating max range: {e}")
        await update.callback_query.answer(f"Error: {str(e)[:50]}")
