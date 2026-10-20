"""Pool detail screen, refresh and add-to-Gateway."""

import logging

from condor.pool_data import (
    fetch_ohlcv,
    gecko_call,
    get_gecko_network,
)
from condor.telegram.transport import as_input_file
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from config_manager import get_client
from utils.telegram_formatters import (
    escape_markdown_v2,
    format_compact_number,
)

from .._shared import (
    DEFAULT_CACHE_TTL,
    get_cached,
    set_cached,
)
from ..visualizations import (
    generate_combined_chart,
    generate_liquidity_chart,
)
from ._common import (
    _fetch_pool_info,
    _fetch_token_balances,
    _require_network,
    get_token_cache_from_gateway,
)
from .add_position.range_math import (
    _calculate_max_range,
    _get_default_range_percent,
    _price_to_percent,
    _validate_bin_range,
)

logger = logging.getLogger(__name__)


async def _show_pool_detail(
    update: Update,
    context: Context,
    pool: dict,
    from_callback: bool = False,
    has_list_context: bool = True,
    timeframe: str = "1h",
) -> None:
    """Show detailed pool information with inline add liquidity controls

    Args:
        update: Telegram update
        context: Bot context
        pool: Pool data dict
        from_callback: Whether triggered from callback (button click)
        has_list_context: Whether there's a pool list to go back to
        timeframe: OHLCV timeframe (1m, 1h, 1d) - default 1h
    """
    import asyncio
    from io import BytesIO

    from utils.telegram_formatters import resolve_token_symbol

    pool_address = pool.get("pool_address", pool.get("address", "N/A"))
    connector = pool.get("connector", "meteora")
    # Every pool dict reaching here is stamped with the network it was found on
    # (pool list, pool-info lookup, position, or GeckoTerminal).
    network = _require_network(pool, "This pool")
    chat_id = update.effective_chat.id

    # Parallel fetch: pool_info, token_cache, and OHLCV data
    cache_key = f"pool_info_{connector}_{pool_address}"
    pool_info = get_cached(context.user_data, cache_key, ttl=DEFAULT_CACHE_TTL)
    token_cache = context.user_data.get("token_cache")
    gecko_network = get_gecko_network(network)

    # Build list of async tasks to run in parallel
    async def fetch_pool_info_task():
        if pool_info is not None:
            return pool_info
        client = await get_client(chat_id, user_data=context.user_data)
        return await _fetch_pool_info(client, pool_address, connector, network)

    async def fetch_token_cache_task():
        if token_cache is not None:
            return token_cache
        return await get_token_cache_from_gateway(chat_id=chat_id, context=context)

    async def fetch_ohlcv_task():
        try:
            data, error = await fetch_ohlcv(
                pool_address=pool_address,
                network=gecko_network,
                timeframe=timeframe,
                currency="token",
            )
            if error:
                logger.warning(f"Failed to fetch OHLCV: {error}")
                return []
            return data or []
        except Exception as e:
            logger.warning(f"Error fetching OHLCV: {e}")
            return []

    # Run all fetches in parallel
    pool_info, token_cache, ohlcv_data = await asyncio.gather(
        fetch_pool_info_task(), fetch_token_cache_task(), fetch_ohlcv_task()
    )

    # Cache results
    if pool_info:
        set_cached(context.user_data, cache_key, pool_info)
    if token_cache:
        context.user_data["token_cache"] = token_cache

    # Pair name comes from the pool LIST (CLMMPoolListItem.trading_pair/name);
    # pool-info carries only token ADDRESSES, so resolve symbols from those.
    pair = pool.get("trading_pair") or pool.get("name")
    mint_x = pool.get("mint_x") or pool_info.get("base_token_address")
    mint_y = pool.get("mint_y") or pool_info.get("quote_token_address")

    if not pair or pair == "N/A":
        if mint_x and mint_y:
            base_symbol = resolve_token_symbol(mint_x, token_cache)
            quote_symbol = resolve_token_symbol(mint_y, token_cache)
            pair = f"{base_symbol}/{quote_symbol}"
            # Store resolved symbols in pool for later use
            pool["base_token"] = mint_x
            pool["quote_token"] = mint_y
        else:
            pair = "Unknown Pair"

    # Extract base/quote symbols - prioritize trading pair over address resolution
    # Trading pair (e.g., "MET-USDC") has accurate symbols, resolve_token_symbol may truncate
    if pair and pair not in ("N/A", "Unknown Pair"):
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
        base_symbol = resolve_token_symbol(mint_x, token_cache) if mint_x else "BASE"
        quote_symbol = resolve_token_symbol(mint_y, token_cache) if mint_y else "QUOTE"

    # Get current price and bin step
    current_price = pool_info.get("price") or pool.get("current_price")
    bin_step = pool.get("bin_step") or pool_info.get("bin_step")
    bins = pool_info.get("bins", [])
    active_bin = pool_info.get("active_bin_id")

    # Initialize or get add_position_params
    if "add_position_params" not in context.user_data:
        context.user_data["add_position_params"] = {}

    params = context.user_data["add_position_params"]

    # Pre-fill params with pool info
    params["connector"] = connector
    params["network"] = network
    params["pool_address"] = pool_address

    # Calculate max range and auto-fill if not set
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
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to calculate range: {e}")

    # Set defaults for amounts
    if not params.get("amount_base"):
        params["amount_base"] = "10%"
    if not params.get("amount_quote"):
        params["amount_quote"] = "10%"
    if not params.get("strategy_type"):
        params["strategy_type"] = "0"

    # Check if tokens are already in gateway (for Add to Gateway button visibility)
    base_in_gateway = mint_x and mint_x in token_cache
    quote_in_gateway = mint_y and mint_y in token_cache
    tokens_in_gateway = base_in_gateway and quote_in_gateway

    # Pool metrics. TVL/volume/fees/APR exist only on the pool LIST item (or a
    # GeckoTerminal pool); pool-info has none of them, so a pool reached by
    # address simply shows no metrics row. The fee percentage does come from
    # pool-info, as fee_pct.
    tvl = pool.get("liquidity")
    vol_24h = pool.get("volume_24h")
    fees_24h = pool.get("fees_24h")
    base_fee = pool.get("base_fee_percentage") or pool_info.get("fee_pct")
    apr = pool.get("apr")

    # Build message - matching show_add_position_menu format
    message = r"➕ *Add CLMM Position*" + "\n\n"

    # Pool header - compact info
    message += f"🏊 *Pool:* `{escape_markdown_v2(pair)}`\n"
    message += f"📍 *Address:*\n`{escape_markdown_v2(pool_address)}`\n"
    if current_price:
        message += f"💱 *Price:* `{escape_markdown_v2(str(current_price)[:10])}`\n"
    if bin_step:
        message += f"📊 *Bin Step:* `{escape_markdown_v2(str(bin_step))}` _\\(default 20 bins each side\\)_\n"
    if base_fee:
        message += f"💸 *Fee:* `{escape_markdown_v2(str(base_fee))}%`\n"

    # Pool metrics section (if available)
    if tvl or vol_24h or fees_24h or apr:
        lines = []
        if tvl:
            lines.append(f"💰 TVL: ${format_compact_number(tvl)}")
        if vol_24h:
            lines.append(f"📈 Vol 24h: ${format_compact_number(vol_24h)}")
        if fees_24h:
            lines.append(f"💵 Fees 24h: ${format_compact_number(fees_24h)}")
        if apr:
            try:
                apr_val = float(apr)
                lines.append(f"📊 APR: {apr_val:.2f}%")
            except (ValueError, TypeError):
                pass
        if lines:
            message += "\n" + escape_markdown_v2(" | ".join(lines)) + "\n"

    # Fetch wallet balances
    try:
        balance_cache_key = f"token_balances_{network}_{base_symbol}_{quote_symbol}"
        balances = get_cached(
            context.user_data, balance_cache_key, ttl=DEFAULT_CACHE_TTL
        )
        if balances is None:
            client = await get_client(chat_id, user_data=context.user_data)
            balances = await _fetch_token_balances(
                client, network, base_symbol, quote_symbol
            )
            set_cached(context.user_data, balance_cache_key, balances)
        context.user_data["token_balances"] = balances
    except Exception as e:
        logger.warning(f"Could not fetch token balances: {e}")
        balances = context.user_data.get(
            "token_balances", {"base_balance": 0, "quote_balance": 0}
        )

    # Store pool for add position and add to gateway
    context.user_data["selected_pool"] = pool
    context.user_data["selected_pool_info"] = pool_info
    context.user_data["dex_state"] = "add_position"

    # ========== WALLET BALANCES ==========
    base_bal = balances.get("base_balance", 0)
    quote_bal = balances.get("quote_balance", 0)
    base_val = balances.get("base_value", 0)
    quote_val = balances.get("quote_value", 0)

    message += "\n" + escape_markdown_v2("━━━ Wallet Balances ━━━") + "\n"
    base_bal_str = format_compact_number(base_bal)
    base_val_str = f"${format_compact_number(base_val)}" if base_val > 0 else ""
    message += f"💰 `{escape_markdown_v2(base_symbol)}`: `{escape_markdown_v2(base_bal_str)}` {escape_markdown_v2(base_val_str)}\n"

    quote_bal_str = format_compact_number(quote_bal)
    quote_val_str = f"${format_compact_number(quote_val)}" if quote_val > 0 else ""
    message += f"💵 `{escape_markdown_v2(quote_symbol)}`: `{escape_markdown_v2(quote_bal_str)}` {escape_markdown_v2(quote_val_str)}\n"

    # ========== POSITION PREVIEW ==========
    message += "\n" + escape_markdown_v2("━━━ Position Preview ━━━") + "\n"

    # Get amounts and calculate actual values
    base_amount_str = params.get("amount_base", "10%")
    quote_amount_str = params.get("amount_quote", "10%")

    try:
        if base_amount_str.endswith("%"):
            base_pct_val = float(base_amount_str[:-1])
            base_amount = base_bal * base_pct_val / 100
        else:
            base_amount = float(base_amount_str) if base_amount_str else 0
    except (ValueError, TypeError):
        base_amount = 0

    try:
        if quote_amount_str.endswith("%"):
            quote_pct_val = float(quote_amount_str[:-1])
            quote_amount = quote_bal * quote_pct_val / 100
        else:
            quote_amount = float(quote_amount_str) if quote_amount_str else 0
    except (ValueError, TypeError):
        quote_amount = 0

    # Show price range with percentages
    lower_pct_preview = params.get("lower_pct")
    upper_pct_preview = params.get("upper_pct")
    lower_price_str = params.get("lower_price", "")
    upper_price_str = params.get("upper_price", "")

    if lower_price_str and upper_price_str:
        try:
            lower_p = float(lower_price_str)
            upper_p = float(upper_price_str)

            # Format prices nicely
            if lower_p >= 1:
                l_str = f"{lower_p:.4f}"
            elif lower_p >= 0.0001:
                l_str = f"{lower_p:.6f}"
            else:
                l_str = f"{lower_p:.8f}"

            if upper_p >= 1:
                u_str = f"{upper_p:.4f}"
            elif upper_p >= 0.0001:
                u_str = f"{upper_p:.6f}"
            else:
                u_str = f"{upper_p:.8f}"

            # Show range with percentages
            l_pct_str = (
                f"({lower_pct_preview:+.1f}%)" if lower_pct_preview is not None else ""
            )
            u_pct_str = (
                f"({upper_pct_preview:+.1f}%)" if upper_pct_preview is not None else ""
            )
            message += f"📉 *Lower:* `{escape_markdown_v2(l_str)}` _{escape_markdown_v2(l_pct_str)}_\n"
            message += f"📈 *Upper:* `{escape_markdown_v2(u_str)}` _{escape_markdown_v2(u_pct_str)}_\n"

            # Validate bin range and show
            if current_price and bin_step:
                is_valid, total_bins, error_msg = _validate_bin_range(
                    lower_p, upper_p, float(current_price), int(bin_step), max_bins=68
                )
                if not is_valid:
                    message += f"⚠️ _{escape_markdown_v2(error_msg)}_\n"
                elif total_bins > 0:
                    message += f"📊 *Bins:* `{total_bins}` _\\(max 68\\)_\n"
        except (ValueError, TypeError):
            pass

    # Show calculated amounts with $ values
    base_amt_fmt = escape_markdown_v2(format_compact_number(base_amount))
    quote_amt_fmt = escape_markdown_v2(format_compact_number(quote_amount))

    # Calculate $ values for amounts using actual token prices from wallet
    try:
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

    message += (
        f"💰 *Add:* `{base_amt_fmt}` {escape_markdown_v2(base_symbol)}{base_usd_str}\n"
    )
    message += f"💵 *Add:* `{quote_amt_fmt}` {escape_markdown_v2(quote_symbol)}{quote_usd_str}\n"

    # Build edit template - each field on its own line
    lower_pct = params.get("lower_pct")
    upper_pct = params.get("upper_pct")
    l_pct_edit = f"{lower_pct:.1f}%" if lower_pct is not None else "-5%"
    # For upper, don't show + prefix (parser treats unsigned as positive)
    u_pct_edit = f"{upper_pct:.1f}%" if upper_pct is not None else "5%"
    base_edit = params.get("amount_base") or "10%"
    quote_edit = params.get("amount_quote") or "10%"

    message += f"\n_To edit, send:_\n"
    message += f"`L:{escape_markdown_v2(l_pct_edit)}`\n"
    message += f"`U:{escape_markdown_v2(u_pct_edit)}`\n"
    message += f"`B:{escape_markdown_v2(base_edit)}`\n"
    message += f"`Q:{escape_markdown_v2(quote_edit)}`\n"

    strategy_display = params.get("strategy_type", "0")
    strategy_names = {"0": "Spot", "1": "Curve", "2": "BidAsk"}
    strategy_name = strategy_names.get(strategy_display, "Spot")

    # Build GeckoTerminal link for pool
    gecko_url = f"https://www.geckoterminal.com/solana/pools/{pool_address}"

    # Timeframe buttons - highlight current
    tf_1m = "• 1m •" if timeframe == "1m" else "1m"
    tf_1h = "• 1h •" if timeframe == "1h" else "1h"
    tf_1d = "• 1d •" if timeframe == "1d" else "1d"

    # Build keyboard - simplified without L/U/B/Q buttons (combined chart is default)
    keyboard = [
        # Row 1: Timeframe selection
        [
            InlineKeyboardButton(text=tf_1m, callback_data="dex:pool_tf:1m"),
            InlineKeyboardButton(text=tf_1h, callback_data="dex:pool_tf:1h"),
            InlineKeyboardButton(text=tf_1d, callback_data="dex:pool_tf:1d"),
        ],
        # Row 2: Strategy + Add Position
        [
            InlineKeyboardButton(
                text=f"🎯 {strategy_name}", callback_data="dex:pos_toggle_strategy"
            ),
            InlineKeyboardButton(
                text="➕ Add Position", callback_data="dex:pos_add_confirm"
            ),
        ],
        # Row 3: Refresh + Gecko link
        [
            InlineKeyboardButton(
                text="🔄 Refresh", callback_data="dex:pool_detail_refresh"
            ),
            InlineKeyboardButton(text="🦎 Gecko", url=gecko_url),
        ],
    ]

    # Only show Add to Gateway if tokens not already in gateway
    if not tokens_in_gateway:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="🔗 Add to Gateway", callback_data="dex:add_to_gateway"
                ),
            ]
        )

    if has_list_context:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="« Back to List", callback_data="dex:pool_list_back"
                ),
                InlineKeyboardButton(text="« LP Menu", callback_data="dex:liquidity"),
            ]
        )
    else:
        keyboard.append(
            [InlineKeyboardButton(text="« LP Menu", callback_data="dex:liquidity")]
        )

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    # Generate combined OHLCV + Liquidity chart (OHLCV already fetched in parallel above)
    chart_bytes = None
    price_float = float(current_price) if current_price else None

    # Parse lower/upper prices for range visualization
    lower_float = None
    upper_float = None
    try:
        if params.get("lower_price"):
            lower_float = float(params["lower_price"])
        if params.get("upper_price"):
            upper_float = float(params["upper_price"])
    except (ValueError, TypeError):
        pass

    # Generate combined chart if we have OHLCV or bins
    if ohlcv_data or bins:
        try:
            chart_bytes = generate_combined_chart(
                ohlcv_data=ohlcv_data or [],
                bins=bins or [],
                pair_name=pair,
                timeframe=timeframe,
                current_price=price_float,
                base_symbol=base_symbol,
                quote_symbol=quote_symbol,
                lower_price=lower_float,
                upper_price=upper_float,
            )
        except Exception as e:
            logger.warning(f"Failed to generate combined chart: {e}")
            # Fallback to liquidity-only chart
            if bins:
                try:
                    chart_bytes = generate_liquidity_chart(
                        bins=bins,
                        active_bin_id=active_bin,
                        current_price=price_float,
                        pair_name=pair,
                        lower_price=lower_float,
                        upper_price=upper_float,
                    )
                except Exception as e2:
                    logger.warning(f"Failed to generate fallback chart: {e2}")

    # Determine chat for sending
    if from_callback:
        chat = update.callback_query.message.chat

        # Check for loading message from timeframe switch and delete it
        loading_msg_id = context.user_data.pop("_loading_msg_id", None)
        loading_chat_id = context.user_data.pop("_loading_chat_id", None)
        if loading_msg_id and loading_chat_id:
            try:
                await context.bot.delete_message(
                    chat_id=loading_chat_id, message_id=loading_msg_id
                )
            except Exception:
                pass
        else:
            # Delete the previous message (normal case, not timeframe switch)
            try:
                await update.callback_query.message.delete()
            except Exception:
                pass
    else:
        chat = update.message.chat
        # Delete user input and original message
        try:
            await update.message.delete()
        except Exception:
            pass
        message_id = context.user_data.get("pool_list_message_id")
        chat_id = context.user_data.get("pool_list_chat_id")
        if message_id and chat_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception:
                pass

    # Send chart as photo with caption, or just text if no chart
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
                caption=message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )
            # Store message ID for back navigation and edits
            context.user_data["pool_detail_message_id"] = sent_msg.message_id
            context.user_data["pool_detail_chat_id"] = chat.id
            context.user_data["add_position_menu_msg_id"] = sent_msg.message_id
            context.user_data["add_position_menu_chat_id"] = chat.id
            context.user_data["add_position_menu_is_photo"] = True
        except Exception as e:
            logger.warning(f"Failed to send chart photo: {e}")
            sent_msg = await chat.send_message(
                text=message, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
            context.user_data["pool_detail_message_id"] = sent_msg.message_id
            context.user_data["pool_detail_chat_id"] = sent_msg.chat.id
            context.user_data["add_position_menu_msg_id"] = sent_msg.message_id
            context.user_data["add_position_menu_chat_id"] = sent_msg.chat.id
            context.user_data["add_position_menu_is_photo"] = False
    else:
        sent_msg = await chat.send_message(
            text=message, parse_mode="MarkdownV2", reply_markup=reply_markup
        )
        context.user_data["pool_detail_message_id"] = sent_msg.message_id
        context.user_data["pool_detail_chat_id"] = sent_msg.chat.id
        context.user_data["add_position_menu_msg_id"] = sent_msg.message_id
        context.user_data["add_position_menu_chat_id"] = sent_msg.chat.id
        context.user_data["add_position_menu_is_photo"] = False


async def handle_pool_select(update: Update, context: Context, pool_index: int) -> None:
    """Handle pool selection from numbered button"""
    query = update.callback_query
    cached_pools = context.user_data.get("pool_list_cache", [])

    if 0 <= pool_index < len(cached_pools):
        pool = cached_pools[pool_index]

        # Show immediate feedback
        pair = pool.get("trading_pair", pool.get("name", "Pool"))
        await query.answer(f"Loading {pair}...")

        # Show loading state in message
        try:
            loading_text = rf"⏳ *Loading pool data\.\.\.*" + "\n\n"
            loading_text += escape_markdown_v2(f"🏊 {pair}") + "\n"
            loading_text += r"_Fetching liquidity bins and pool info\.\.\._"
            await query.message.edit_text(loading_text, parse_mode="MarkdownV2")
        except Exception:
            pass

        await _show_pool_detail(update, context, pool, from_callback=True)
    else:
        await query.answer("Pool not found. Please search again.")


async def handle_pool_detail_refresh(
    update: Update, context: Context, timeframe: str = None
) -> None:
    """Refresh pool detail by clearing cache and re-fetching

    Args:
        timeframe: Optional OHLCV timeframe (1m, 1h, 1d). If None, uses default 1h.
    """
    from .._shared import clear_cache

    query = update.callback_query
    selected_pool = context.user_data.get("selected_pool", {})
    if not selected_pool:
        await query.answer("No pool selected")
        return

    pool_address = selected_pool.get("pool_address", selected_pool.get("address", ""))
    connector = selected_pool.get("connector", "meteora")

    # Only clear cache on full refresh (no timeframe switch)
    if timeframe is None:
        # Clear pool info cache
        cache_key = f"pool_info_{connector}_{pool_address}"
        clear_cache(context.user_data, cache_key)
        context.user_data.pop("selected_pool_info", None)

        # Also clear add_position_params to get fresh range calculation
        context.user_data.pop("add_position_params", None)
        await query.answer("Refreshing...")
        timeframe = "1h"  # Default timeframe
    else:
        # Store current timeframe in add_position_params to persist across param changes
        if "add_position_params" not in context.user_data:
            context.user_data["add_position_params"] = {}
        context.user_data["add_position_params"]["timeframe"] = timeframe
        # Timeframe switch - show loading transition
        await query.answer(f"Loading {timeframe} candles...")

        # Show loading state - need to handle both photo and text messages
        pair = selected_pool.get("trading_pair", selected_pool.get("name", "Pool"))
        loading_msg = rf"⏳ *Loading {timeframe} candles\.\.\.*" + "\n\n"
        loading_msg += f"🏊 *Pool:* `{escape_markdown_v2(pair)}`\n"
        loading_msg += r"_Fetching OHLCV data\.\.\._"

        loading_keyboard = [
            [InlineKeyboardButton(text="⏳ Loading...", callback_data="noop")]
        ]

        try:
            # Try to delete current message (works for both photo and text)
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

    await _show_pool_detail(
        update, context, selected_pool, from_callback=True, timeframe=timeframe
    )


async def handle_add_to_gateway(update: Update, context: Context) -> None:
    """Add pool tokens to Gateway token list

    Fetches token details from GeckoTerminal and adds both base and quote tokens
    to the Gateway configuration for the network.
    """
    from config_manager import get_config_manager

    query = update.callback_query

    selected_pool = context.user_data.get("selected_pool", {})
    pool_info = context.user_data.get("selected_pool_info", {})

    if not selected_pool:
        await query.answer("No pool selected")
        return

    # Get token addresses (pool list: mint_x/mint_y; pool-info: *_token_address)
    mint_x = selected_pool.get("mint_x") or pool_info.get("base_token_address")
    mint_y = selected_pool.get("mint_y") or pool_info.get("quote_token_address")

    if not mint_x or not mint_y:
        await query.answer("Token addresses not available", show_alert=True)
        return

    network_id = _require_network(selected_pool, "This pool")
    gecko_network = get_gecko_network(network_id)

    # Show loading
    await query.answer("Adding tokens to Gateway...")

    try:
        # Edit message to show progress
        (
            await query.message.edit_caption(
                caption=escape_markdown_v2("🔄 Adding tokens to Gateway..."),
                parse_mode="MarkdownV2",
            )
            if getattr(query.message, "photo", None)
            else await query.message.edit_text(
                escape_markdown_v2("🔄 Adding tokens to Gateway..."),
                parse_mode="MarkdownV2",
            )
        )
    except Exception:
        pass

    added_tokens = []
    errors = []

    async def add_token_to_gateway(token_address: str) -> bool:
        """Fetch token info and add to gateway"""
        try:
            # Fetch from GeckoTerminal
            result = await gecko_call(
                "get_specific_token_on_network", gecko_network, token_address
            )

            if isinstance(result, dict):
                token_data = result.get("data", result) if "data" in result else result
                attrs = token_data.get("attributes", token_data)
                symbol = attrs.get("symbol", "???")
                decimals = attrs.get("decimals", 9)
                name = attrs.get("name")

                # Add to gateway
                client = await get_config_manager().get_client()
                await client.gateway.add_token(
                    network_id=network_id,
                    address=token_address,
                    symbol=symbol,
                    decimals=decimals,
                    name=name,
                )
                return symbol
            return None
        except Exception as e:
            error_str = str(e)
            # Ignore "already exists" errors
            if (
                "already exists" in error_str.lower()
                or "duplicate" in error_str.lower()
            ):
                return "exists"
            logger.warning(f"Failed to add token {token_address[:12]}...: {e}")
            return None

    # Add both tokens
    result_x = await add_token_to_gateway(mint_x)
    if result_x and result_x != "exists":
        added_tokens.append(result_x)
    elif result_x == "exists":
        pass  # Token already exists, that's fine
    else:
        errors.append(f"base ({mint_x[:8]}...)")

    result_y = await add_token_to_gateway(mint_y)
    if result_y and result_y != "exists":
        added_tokens.append(result_y)
    elif result_y == "exists":
        pass  # Token already exists
    else:
        errors.append(f"quote ({mint_y[:8]}...)")

    # Build result message
    if added_tokens:
        success_msg = f"✅ Added: {', '.join(added_tokens)}"
    else:
        success_msg = "ℹ️ Tokens already in Gateway"

    if errors:
        success_msg += f"\n⚠️ Failed: {', '.join(errors)}"

    success_msg += "\n\n⚠️ Restart Gateway for changes to take effect"

    # Go back to pool detail
    try:
        (
            await query.message.edit_caption(
                caption=escape_markdown_v2(success_msg), parse_mode="MarkdownV2"
            )
            if getattr(query.message, "photo", None)
            else await query.message.edit_text(
                escape_markdown_v2(success_msg), parse_mode="MarkdownV2"
            )
        )
    except Exception:
        pass

    # After short delay, refresh pool detail
    import asyncio

    await asyncio.sleep(2)
    await _show_pool_detail(update, context, selected_pool, from_callback=True)
