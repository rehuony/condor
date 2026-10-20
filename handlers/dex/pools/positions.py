"""CLMM position views, fee collection and closing."""

import logging

from condor.pool_data import (
    fetch_ohlcv,
    get_gecko_network,
)
from condor.preferences import set_dex_last_pool
from condor.telegram.errors import BadRequest, is_not_modified
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
    format_amount,
    format_error_message,
    resolve_token_symbol,
)

from .._shared import (
    DEFAULT_CACHE_TTL,
    get_cached,
    invalidate_cache,
    set_cached,
)
from ..visualizations import (
    generate_combined_chart,
)
from ._common import (
    _fetch_pool_info,
    _require_network,
    format_pair_from_addresses,
    get_dex_pool_url,
    get_token_cache_from_gateway,
)
from .pool_detail import _show_pool_detail

logger = logging.getLogger(__name__)


def _format_position_detail(
    pos: dict,
    token_cache: dict = None,
    detailed: bool = False,
    token_prices: dict = None,
) -> str:
    """
    Format a single position for display.

    Args:
        pos: Position data dictionary
        token_cache: Optional token address->symbol mapping
        detailed: If True, show full details; if False, show compact summary
        token_prices: Optional token symbol -> USD price mapping

    Returns:
        Formatted position string (not escaped)
    """
    token_cache = token_cache or {}
    token_prices = token_prices or {}

    # Resolve token addresses to symbols
    base_token = pos.get("base_token", pos.get("token_a", ""))
    quote_token = pos.get("quote_token", pos.get("token_b", ""))
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)

    connector = pos.get("connector", "unknown")
    pool_address = pos.get("pool_address", "")

    # Get current amounts
    base_amount = pos.get(
        "base_token_amount", pos.get("amount_a", pos.get("token_a_amount", 0))
    )
    quote_amount = pos.get(
        "quote_token_amount", pos.get("amount_b", pos.get("token_b_amount", 0))
    )

    # Get price range
    lower = pos.get("lower_price", pos.get("price_lower", ""))
    upper = pos.get("upper_price", pos.get("price_upper", ""))

    # Get in-range status
    in_range = pos.get("in_range", "")
    range_emoji = (
        "🟢" if in_range == "IN_RANGE" else "🔴" if in_range == "OUT_OF_RANGE" else "⚪"
    )

    # Get PNL data
    # hapi emits pnl_summary: null when entry/current price or initial amounts
    # are missing, so a {} default on a present key never applies.
    pnl_summary = pos.get("pnl_summary") or {}

    # Get pending fees
    base_fee = pos.get(
        "base_fee_pending", pos.get("unclaimed_fee_a", pos.get("fees_a", 0))
    )
    quote_fee = pos.get(
        "quote_fee_pending", pos.get("unclaimed_fee_b", pos.get("fees_b", 0))
    )

    lines = []

    # Header: pair with connector
    pair_display = f"{base_symbol}-{quote_symbol}"
    lines.append(f"🏊 {pair_display} ({connector})")

    if detailed:
        # Full detailed view
        if pool_address:
            lines.append(f"📍 Pool: `{pool_address}`")

        # Range with status indicator - show appropriate decimals based on price magnitude
        if lower and upper:
            try:
                lower_f = float(lower)
                upper_f = float(upper)
                if lower_f >= 1:
                    decimals = 2
                elif lower_f >= 0.01:
                    decimals = 4
                elif lower_f >= 0.0001:
                    decimals = 6
                else:
                    decimals = 8
                range_str = f"{lower_f:.{decimals}f} - {upper_f:.{decimals}f}"
                lines.append(f"{range_emoji} Range: [{range_str}]")
            except (ValueError, TypeError):
                lines.append(f"{range_emoji} Range: [{lower} - {upper}]")

        # Current price and entry price from pnl_summary
        entry_price = pnl_summary.get("entry_price")
        current_price = pnl_summary.get("current_price") or pos.get("current_price")
        if entry_price and current_price:
            try:
                price_change = pnl_summary.get("price_change_pct", 0) or 0
                sign = "+" if price_change >= 0 else ""
                lines.append(
                    f"💱 Price: {float(current_price):.6f} (entry: {float(entry_price):.6f}, {sign}{price_change:.2f}%)"
                )
            except (ValueError, TypeError):
                pass

        lines.append("")  # Separator

        # Get token prices for USD conversion (try exact match, then variants)
        def get_price(symbol, default=0):
            if symbol in token_prices:
                return token_prices[symbol]
            # Try case-insensitive match
            symbol_lower = symbol.lower()
            for key, price in token_prices.items():
                if key.lower() == symbol_lower:
                    return price
            # Try common variants (WSOL <-> SOL, WETH <-> ETH, etc.)
            variants = {
                "sol": ["wsol", "wrapped sol"],
                "wsol": ["sol"],
                "eth": ["weth", "wrapped eth"],
                "weth": ["eth"],
            }
            for variant in variants.get(symbol_lower, []):
                for key, price in token_prices.items():
                    if key.lower() == variant:
                        return price
            return default

        quote_price_f = get_price(quote_symbol, 1.0)
        base_price_f = get_price(base_symbol, 0)

        # Current holdings
        if base_amount or quote_amount:
            try:
                base_amt_str = format_amount(float(base_amount))
                quote_amt_str = format_amount(float(quote_amount))
                lines.append(f"━━━ Holdings ━━━")
                lines.append(
                    f"💰 {base_amt_str} {base_symbol} / {quote_amt_str} {quote_symbol}"
                )
            except (ValueError, TypeError):
                pass

        # Value information from pnl_summary - convert to USD
        initial_value = pnl_summary.get("initial_value_quote")
        current_value = pnl_summary.get("current_lp_value_quote") or pnl_summary.get(
            "current_total_value_quote"
        )
        if initial_value and current_value:
            try:
                initial_usd = float(initial_value) * quote_price_f
                current_usd = float(current_value) * quote_price_f
                lines.append(
                    f"💵 Value: ${current_usd:.2f} (initial: ${initial_usd:.2f})"
                )
            except (ValueError, TypeError):
                pass

        lines.append("")  # Separator
        lines.append("━━━ Performance ━━━")

        # PnL from pnl_summary - convert to USD
        total_pnl = pnl_summary.get("total_pnl_quote")
        total_pnl_pct = pnl_summary.get("total_pnl_pct")
        if total_pnl is not None:
            try:
                pnl_val = float(total_pnl) * quote_price_f
                pnl_pct = float(total_pnl_pct) if total_pnl_pct else 0
                emoji = "📈" if pnl_val >= 0 else "📉"
                sign = "+" if pnl_val >= 0 else ""
                lines.append(
                    f"{emoji} PnL: {sign}${pnl_val:.2f} ({sign}{pnl_pct:.2f}%)"
                )
            except (ValueError, TypeError):
                pass

        # Impermanent loss - convert to USD
        il = pnl_summary.get("impermanent_loss_quote")
        if il is not None:
            try:
                il_val = float(il) * quote_price_f
                if il_val != 0:
                    lines.append(f"⚠️ IL: ${il_val:.2f}")
            except (ValueError, TypeError):
                pass

        # Fees - show pending and collected separately in USD
        try:
            # Get fee amounts
            base_fee_pending = float(pos.get("base_fee_pending", 0) or 0)
            quote_fee_pending = float(pos.get("quote_fee_pending", 0) or 0)
            base_fee_collected = float(pos.get("base_fee_collected", 0) or 0)
            quote_fee_collected = float(pos.get("quote_fee_collected", 0) or 0)

            # Convert to USD
            pending_usd = (base_fee_pending * base_price_f) + (
                quote_fee_pending * quote_price_f
            )
            collected_usd = (base_fee_collected * base_price_f) + (
                quote_fee_collected * quote_price_f
            )

            # Show pending fees (available to collect)
            if pending_usd > 0.01:
                lines.append(f"🎁 Pending fees: ${pending_usd:.2f}")

            # Show collected fees (already claimed)
            if collected_usd > 0.01:
                lines.append(f"💰 Collected fees: ${collected_usd:.2f}")

            # If no fees at all, show zero
            if pending_usd <= 0.01 and collected_usd <= 0.01:
                lines.append(f"💰 Fees: $0.00")
        except (ValueError, TypeError):
            pass

        # Duration and APR
        duration_hours = pnl_summary.get("duration_hours")
        fee_apr = pnl_summary.get("fee_apr_estimate")
        if duration_hours is not None:
            try:
                hours = float(duration_hours)
                if hours < 24:
                    duration_str = f"{hours:.1f}h"
                else:
                    days = hours / 24
                    duration_str = f"{days:.1f}d"
                lines.append(f"⏱️ Duration: {duration_str}")
            except (ValueError, TypeError):
                pass

        if fee_apr is not None:
            try:
                apr_val = float(fee_apr)
                lines.append(f"📊 APR estimate: {apr_val:.2f}%")
            except (ValueError, TypeError):
                pass

    else:
        # Compact summary view
        range_str = ""
        if lower and upper:
            try:
                lower_f = float(lower)
                upper_f = float(upper)
                if lower_f >= 1:
                    range_str = f"[{lower_f:.2f}-{upper_f:.2f}]"
                else:
                    range_str = f"[{lower_f:.3f}-{upper_f:.3f}]"
            except (ValueError, TypeError):
                range_str = f"[{lower}-{upper}]"

        # Show current holdings compactly
        try:
            base_amt = float(base_amount) if base_amount else 0
            quote_amt = float(quote_amount) if quote_amount else 0
            if base_amt > 0 or quote_amt > 0:
                lines.append(
                    f"   {range_emoji} {range_str} | {format_amount(base_amt)} {base_symbol} / {format_amount(quote_amt)} {quote_symbol}"
                )
            else:
                lines.append(f"   {range_emoji} Range: {range_str}")
        except (ValueError, TypeError):
            lines.append(f"   {range_emoji} Range: {range_str}")

        # Compact PNL (quote-denominated, as hapi calculates it)
        compact_pnl = pnl_summary.get("total_pnl_quote")
        if compact_pnl is not None:
            sign = "+" if compact_pnl >= 0 else ""
            lines.append(f"   📊 {sign}{format_amount(compact_pnl)} {quote_symbol}")

        # Compact Fees - show if any fees are pending
        try:
            base_fee_f = float(base_fee) if base_fee else 0
            quote_fee_f = float(quote_fee) if quote_fee else 0
            if base_fee_f > 0 or quote_fee_f > 0:
                fee_parts = []
                if base_fee_f > 0:
                    fee_parts.append(f"{format_amount(base_fee_f)} {base_symbol}")
                if quote_fee_f > 0:
                    fee_parts.append(f"{format_amount(quote_fee_f)} {quote_symbol}")
                lines.append(f"   🎁 Fees: {' / '.join(fee_parts)}")
        except (ValueError, TypeError):
            pass

    return "\n".join(lines)


async def handle_manage_positions(update: Update, context: Context) -> None:
    """Display manage positions menu with all active LP positions"""
    try:
        chat_id = update.effective_chat.id
        client = await get_client(chat_id, user_data=context.user_data)

        if not hasattr(client, "gateway_clmm"):
            raise ValueError("Gateway CLMM not available")

        # Fetch token cache for symbol resolution
        token_cache = await get_token_cache_from_gateway(
            chat_id=chat_id, context=context
        )
        context.user_data["token_cache"] = token_cache

        # Fetch all open positions
        result = await client.gateway_clmm.search_positions(
            limit=50, offset=0, status="OPEN"
        )

        all_positions = result.get("data", []) if result else []

        # Filter out CLOSED positions and positions with 0 liquidity
        def is_active_position(pos):
            # Check status field first - exclude CLOSED positions
            status = pos.get("status", "").upper()
            if status == "CLOSED":
                return False

            # Check liquidity
            liq = pos.get("liquidity") or pos.get("current_liquidity")
            if liq is not None:
                try:
                    return float(liq) > 0
                except (ValueError, TypeError):
                    pass
            # Check token amounts as fallback (use correct field names)
            base = (
                pos.get("base_token_amount")
                or pos.get("base_amount")
                or pos.get("amount_base")
            )
            quote = (
                pos.get("quote_token_amount")
                or pos.get("quote_amount")
                or pos.get("amount_quote")
            )
            if base is not None or quote is not None:
                try:
                    base_val = float(base) if base is not None else 0
                    quote_val = float(quote) if quote is not None else 0
                    return base_val > 0 or quote_val > 0
                except (ValueError, TypeError):
                    pass
            return True  # Assume active if we can't determine

        positions = [p for p in all_positions if is_active_position(p)]
        if len(positions) < len(all_positions):
            logger.info(
                f"Filtered {len(all_positions) - len(positions)} closed/empty positions"
            )

        # Build message
        if positions:
            header = rf"📍 *Manage LP Positions* \({len(positions)} active\)" + "\n\n"

            for i, pos in enumerate(positions[:10]):  # Show top 10
                pos_detail = _format_position_detail(
                    pos, token_cache=token_cache, detailed=False
                )
                # Add index number prefix to correlate with buttons
                header += (
                    escape_markdown_v2(f"#{i+1} ")
                    + escape_markdown_v2(pos_detail)
                    + "\n\n"
                )

            if len(positions) > 10:
                header += escape_markdown_v2(
                    f"... and {len(positions) - 10} more positions"
                )
        else:
            header = r"📍 *Manage LP Positions*" + "\n\n"
            header += r"_No active positions found\._" + "\n\n"
            header += r"Use ➕ *New Position* to add liquidity to a pool\."

        # Build keyboard with position actions
        keyboard = []

        # Initialize positions cache
        if "positions_cache" not in context.user_data:
            context.user_data["positions_cache"] = {}

        # Add buttons for each position (max 5 for manageability)
        for i, pos in enumerate(positions[:5]):
            # Resolve pair name for button label
            base_token = pos.get("base_token", pos.get("token_a", ""))
            quote_token = pos.get("quote_token", pos.get("token_b", ""))
            pair = format_pair_from_addresses(base_token, quote_token, token_cache)[:10]

            # Store position info in context for later use
            context.user_data["positions_cache"][str(i)] = pos

            # Use index number to distinguish positions with same pair
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=f"#{i+1} {pair}", callback_data=f"dex:pos_view:{i}"
                    ),
                    InlineKeyboardButton(
                        text="💰 Fees", callback_data=f"dex:pos_collect:{i}"
                    ),
                    InlineKeyboardButton(
                        text="❌ Close", callback_data=f"dex:pos_close:{i}"
                    ),
                ]
            )

        # Add new position and back buttons
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="➕ New Position", callback_data="dex:add_position"
                ),
                InlineKeyboardButton(text="« Back", callback_data="dex:liquidity"),
            ]
        )

        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        try:
            await update.callback_query.message.edit_text(
                header, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
        except BadRequest as e:
            if not is_not_modified(e):
                raise

    except Exception as e:
        logger.error(f"Error loading positions: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to load positions: {str(e)}")
        keyboard = [
            [InlineKeyboardButton(text="« Back", callback_data="dex:liquidity")]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await update.callback_query.message.edit_text(
            error_message, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def handle_pos_view(
    update: Update,
    context: Context,
    pos_index: str,
    timeframe: str = "1h",
) -> None:
    """View detailed info about a position with combined OHLCV + liquidity chart

    Args:
        update: Telegram update
        context: Bot context
        pos_index: Position index in cache
        timeframe: OHLCV timeframe (1m, 1h, 1d) - default 1h
    """
    import asyncio
    from io import BytesIO

    query = update.callback_query

    try:
        positions_cache = context.user_data.get("positions_cache", {})
        pos = positions_cache.get(pos_index)

        if not pos:
            await query.answer("Position not found. Please refresh.")
            return

        await query.answer()

        # Get basic position info for loading message
        base_token = pos.get("base_token", pos.get("token_a", ""))
        quote_token = pos.get("quote_token", pos.get("token_b", ""))
        token_cache = context.user_data.get("token_cache", {})
        from utils.telegram_formatters import resolve_token_symbol

        base_symbol = (
            resolve_token_symbol(base_token, token_cache) if base_token else "BASE"
        )
        quote_symbol = (
            resolve_token_symbol(quote_token, token_cache) if quote_token else "QUOTE"
        )
        pair = f"{base_symbol}/{quote_symbol}"

        # Immediately show loading state
        loading_msg = f"📍 *Position: {escape_markdown_v2(pair)}*\n\n"
        loading_msg += r"⏳ _Gathering information\.\.\._" + "\n"
        loading_msg += r"• Fetching OHLCV data" + "\n"
        loading_msg += r"• Loading liquidity distribution" + "\n"
        loading_msg += r"• Generating chart"

        loading_keyboard = [
            [InlineKeyboardButton(text="⏳ Loading...", callback_data="noop")]
        ]

        # Handle loading state for both photo and text messages
        loading_msg_id = None
        if query.message.photo:
            # For photo messages, delete and send new text message
            try:
                await query.message.delete()
            except Exception:
                pass
            loading_sent = await query.message.answer(
                loading_msg,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=loading_keyboard),
            )
            loading_msg_id = loading_sent.message_id
        else:
            # For text messages, just edit
            try:
                await query.message.edit_text(
                    loading_msg,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=loading_keyboard),
                )
            except Exception:
                pass

        chat_id = update.effective_chat.id
        connector = pos.get("connector", "meteora")
        pool_address = pos.get("pool_address", "")
        network = _require_network(pos, "This position")

        # Get position price data
        lower_price = pos.get("lower_price", pos.get("price_lower"))
        upper_price = pos.get("upper_price", pos.get("price_upper"))
        pnl_summary = pos.get("pnl_summary") or {}
        entry_price = pnl_summary.get("entry_price")
        current_price = pnl_summary.get("current_price")

        # Parallel fetch: token_cache (if needed), pool_info (bins), and OHLCV
        gecko_network = get_gecko_network(network)

        async def fetch_token_cache_task():
            cached = context.user_data.get("token_cache")
            if cached is not None:
                return cached
            return await get_token_cache_from_gateway(chat_id=chat_id, context=context)

        async def fetch_pool_info_task():
            if not pool_address:
                return {}
            cache_key = f"pool_info_{connector}_{pool_address}"
            cached = get_cached(context.user_data, cache_key, ttl=DEFAULT_CACHE_TTL)
            if cached:
                return cached
            client = await get_client(chat_id, user_data=context.user_data)
            info = await _fetch_pool_info(client, pool_address, connector, network)
            if info:
                set_cached(context.user_data, cache_key, info)
            return info or {}

        async def fetch_ohlcv_task():
            if not pool_address:
                return []
            try:
                data, error = await fetch_ohlcv(
                    pool_address=pool_address,
                    network=gecko_network,
                    timeframe=timeframe,
                    currency="token",
                )
                return data or []
            except Exception as e:
                logger.warning(f"Error fetching OHLCV for position: {e}")
                return []

        # Run all fetches in parallel
        token_cache_new, pool_info, ohlcv_data = await asyncio.gather(
            fetch_token_cache_task(), fetch_pool_info_task(), fetch_ohlcv_task()
        )

        # Update token cache if fetched
        if token_cache_new:
            token_cache = token_cache_new
            context.user_data["token_cache"] = token_cache

        # Re-resolve symbols with fresh token cache if needed
        if token_cache_new and token_cache_new != context.user_data.get("token_cache"):
            base_symbol = (
                resolve_token_symbol(base_token, token_cache) if base_token else "BASE"
            )
            quote_symbol = (
                resolve_token_symbol(quote_token, token_cache)
                if quote_token
                else "QUOTE"
            )
            pair = f"{base_symbol}/{quote_symbol}"

        # Get bins from pool_info
        bins = pool_info.get("bins", [])

        # Get current price from pool_info if not in pnl_summary
        if not current_price:
            current_price = pool_info.get("price")

        # Get token prices for USD conversion
        token_prices = context.user_data.get("token_prices", {})

        # Format detailed view with full information
        detail = _format_position_detail(
            pos, token_cache=token_cache, detailed=True, token_prices=token_prices
        )
        message = r"📍 *Position Details*" + "\n\n"
        message += escape_markdown_v2(detail)

        # Build keyboard with actions
        dex_url = get_dex_pool_url(connector, pool_address)

        # Timeframe buttons - highlight current
        tf_1m = "• 1m •" if timeframe == "1m" else "1m"
        tf_1h = "• 1h •" if timeframe == "1h" else "1h"
        tf_1d = "• 1d •" if timeframe == "1d" else "1d"

        keyboard = [
            # Row 1: Timeframe selection
            [
                InlineKeyboardButton(
                    text=tf_1m, callback_data=f"dex:pos_view_tf:{pos_index}:1m"
                ),
                InlineKeyboardButton(
                    text=tf_1h, callback_data=f"dex:pos_view_tf:{pos_index}:1h"
                ),
                InlineKeyboardButton(
                    text=tf_1d, callback_data=f"dex:pos_view_tf:{pos_index}:1d"
                ),
            ],
            # Row 2: Actions
            [
                InlineKeyboardButton(
                    text="💰 Collect Fees", callback_data=f"dex:pos_collect:{pos_index}"
                ),
                InlineKeyboardButton(
                    text="❌ Close", callback_data=f"dex:pos_close:{pos_index}"
                ),
            ],
        ]

        # Add DEX link if available
        if dex_url:
            keyboard.append(
                [
                    InlineKeyboardButton(text=f"🌐 {connector.title()}", url=dex_url),
                    InlineKeyboardButton(text="« Back", callback_data="dex:liquidity"),
                ]
            )
        else:
            keyboard.append(
                [InlineKeyboardButton(text="« Back", callback_data="dex:liquidity")]
            )

        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        # Generate combined chart with entry price, lower/upper bounds
        chart_bytes = None
        if ohlcv_data or bins:
            try:
                # Convert price strings to floats
                lower_float = float(lower_price) if lower_price else None
                upper_float = float(upper_price) if upper_price else None
                entry_float = float(entry_price) if entry_price else None
                current_float = float(current_price) if current_price else None

                chart_bytes = generate_combined_chart(
                    ohlcv_data=ohlcv_data or [],
                    bins=bins or [],
                    pair_name=pair,
                    timeframe=timeframe,
                    current_price=current_float,
                    base_symbol=base_symbol,
                    quote_symbol=quote_symbol,
                    lower_price=lower_float,
                    upper_price=upper_float,
                    entry_price=entry_float,
                )
            except Exception as e:
                logger.warning(f"Failed to generate position chart: {e}")

        # Send as photo with caption, or edit text if no chart
        if chart_bytes:
            try:
                # Handle both BytesIO and raw bytes
                if isinstance(chart_bytes, BytesIO):
                    photo_file = chart_bytes
                    photo_file.seek(0)
                else:
                    photo_file = BytesIO(chart_bytes)
                photo_file.name = "position_chart.png"

                # Delete loading message (either the text loading message we sent or original message)
                if loading_msg_id:
                    # Delete the loading text message we sent
                    try:
                        await query.message.chat.delete_message(loading_msg_id)
                    except Exception:
                        pass
                else:
                    # Delete the original message (text message case)
                    try:
                        await query.message.delete()
                    except Exception:
                        pass

                await query.message.answer_photo(
                    photo=as_input_file(photo_file),
                    caption=message,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup,
                )
            except Exception as e:
                logger.warning(f"Failed to send position chart: {e}")
                # Fallback to text
                await query.message.answer(
                    message, parse_mode="MarkdownV2", reply_markup=reply_markup
                )
        else:
            # No chart available, just update or send message with details
            if loading_msg_id:
                # Delete loading message and send new
                try:
                    await query.message.chat.delete_message(loading_msg_id)
                except Exception:
                    pass
                await query.message.answer(
                    message, parse_mode="MarkdownV2", reply_markup=reply_markup
                )
            else:
                # Try to edit the text message
                try:
                    await query.message.edit_text(
                        message, parse_mode="MarkdownV2", reply_markup=reply_markup
                    )
                except Exception:
                    # Message was deleted, send new one
                    await query.message.answer(
                        message, parse_mode="MarkdownV2", reply_markup=reply_markup
                    )

    except Exception as e:
        logger.error(f"Error viewing position: {e}", exc_info=True)
        await query.answer(f"Error: {str(e)[:100]}")


async def handle_pos_view_pool(
    update: Update, context: Context, pos_index: str
) -> None:
    """View pool details from a position - shows pool with liquidity chart"""
    try:
        positions_cache = context.user_data.get("positions_cache", {})
        pos = positions_cache.get(pos_index)

        if not pos:
            await update.callback_query.answer("Position not found. Please refresh.")
            return

        pool_address = pos.get("pool_address", "")
        connector = pos.get("connector", "meteora")

        if not pool_address:
            await update.callback_query.answer(
                "Pool address not available", show_alert=True
            )
            return

        # Build pool dict for _show_pool_detail
        pool = {
            "pool_address": pool_address,
            "address": pool_address,
            "connector": connector,
            "network": _require_network(pos, "This position"),
            "trading_pair": pos.get("trading_pair"),
            "mint_x": pos.get("base_token"),
            "mint_y": pos.get("quote_token"),
        }

        # Store reference back to position
        context.user_data["viewing_position_index"] = pos_index

        await update.callback_query.answer("Loading pool details...")
        await _show_pool_detail(
            update, context, pool, from_callback=True, has_list_context=False
        )

    except Exception as e:
        logger.error(f"Error viewing pool from position: {e}", exc_info=True)
        await update.callback_query.answer(f"Error: {str(e)[:100]}")


async def handle_pos_collect_fees(
    update: Update, context: Context, pos_index: str
) -> None:
    """Collect fees from a position - shows progress inline then returns to positions"""
    import asyncio

    query = update.callback_query

    try:
        positions_cache = context.user_data.get("positions_cache", {})
        pos = positions_cache.get(pos_index)

        if not pos:
            await query.answer("Position not found. Please refresh.")
            return

        # Get token cache for better pair display
        token_cache = context.user_data.get("token_cache", {})
        base_token = pos.get("base_token", pos.get("token_a", ""))
        quote_token = pos.get("quote_token", pos.get("token_b", ""))
        pair = format_pair_from_addresses(base_token, quote_token, token_cache)

        # Edit message to show collecting status
        await query.answer()
        try:
            await query.message.edit_text(
                f"⏳ Collecting fees from {escape_markdown_v2(pair)}\\.\\.\\.",
                parse_mode="MarkdownV2",
                reply_markup=None,
            )
        except Exception as edit_err:
            if "no text in the message" in str(edit_err).lower():
                # Message is likely a photo/media - send a new message instead
                await query.message.answer(
                    f"⏳ Collecting fees from {escape_markdown_v2(pair)}\\.\\.\\.",
                    parse_mode="MarkdownV2",
                )
            else:
                raise

        chat_id = update.effective_chat.id
        client = await get_client(chat_id, user_data=context.user_data)

        if not hasattr(client, "gateway_clmm"):
            raise ValueError("Gateway CLMM not available")

        # Get position details
        connector = pos.get("connector", "meteora")
        network = pos.get("network", "solana-mainnet-beta")
        position_address = pos.get("position_address", pos.get("nft_id", ""))

        # Call collect fees with 30s timeout
        try:
            result = await asyncio.wait_for(
                client.gateway_clmm.collect_fees(
                    connector=connector,
                    network=network,
                    position_address=position_address,
                ),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                "Operation timed out. Check your connection to the backend."
            )

        # Build back button
        back_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="« Back", callback_data="dex:liquidity")]
            ]
        )

        # Always invalidate caches after API call - even if result is empty,
        # the transaction was sent and we need fresh data
        invalidate_cache(context.user_data, "positions", "balances")
        # Also clear the local position caches used for quick lookups
        context.user_data.pop("positions_cache", None)
        context.user_data.pop("lp_positions_cache", None)

        if result:
            success_msg = f"✅ *Fees collected from {escape_markdown_v2(pair)}\\!*"
            if isinstance(result, dict):
                tx_hash = result.get("transaction_hash")
                if tx_hash:
                    success_msg += f"\n\nTx: `{tx_hash[:30]}...`"

            try:
                await query.message.edit_text(
                    success_msg, parse_mode="MarkdownV2", reply_markup=back_keyboard
                )
            except Exception:
                await query.message.answer(
                    success_msg, parse_mode="MarkdownV2", reply_markup=back_keyboard
                )
        else:
            # A successful collect always returns a populated
            # CLMMCollectFeesResponse, so an empty result is an API failure —
            # not a zero-fee collect.
            failure_msg = (
                f"❌ No response from the API collecting fees on "
                f"{escape_markdown_v2(pair)}"
            )
            try:
                await query.message.edit_text(
                    failure_msg,
                    parse_mode="MarkdownV2",
                    reply_markup=back_keyboard,
                )
            except Exception:
                await query.message.answer(
                    failure_msg,
                    parse_mode="MarkdownV2",
                    reply_markup=back_keyboard,
                )

    except Exception as e:
        logger.error(f"Error collecting fees: {e}", exc_info=True)

        # Build error message with back button
        error_msg = str(e)
        if "timeout" in error_msg.lower() or "Timeout" in error_msg:
            display_error = "Operation timed out\\. The transaction may still be processing on\\-chain\\."
        else:
            display_error = escape_markdown_v2(error_msg[:150])

        back_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔄 Retry", callback_data=f"dex:pos_collect:{pos_index}"
                    )
                ],
                [InlineKeyboardButton(text="« Back", callback_data="dex:liquidity")],
            ]
        )

        try:
            await query.message.edit_text(
                f"❌ *Failed to collect fees*\n\n{display_error}",
                parse_mode="MarkdownV2",
                reply_markup=back_keyboard,
            )
        except Exception as edit_error:
            logger.warning(f"Could not edit message: {edit_error}")
            try:
                await query.message.answer(
                    f"❌ *Failed to collect fees*\n\n{display_error}",
                    parse_mode="MarkdownV2",
                    reply_markup=back_keyboard,
                )
            except Exception:
                pass  # Last resort - at least don't crash


async def handle_pos_close_confirm(
    update: Update, context: Context, pos_index: str
) -> None:
    """Show confirmation for closing a position"""
    try:
        positions_cache = context.user_data.get("positions_cache", {})
        pos = positions_cache.get(pos_index)

        if not pos:
            await update.callback_query.answer("Position not found. Please refresh.")
            return

        # Get token cache and prices for display
        token_cache = context.user_data.get("token_cache") or {}
        token_prices = context.user_data.get("token_prices", {})
        detail = _format_position_detail(
            pos, token_cache=token_cache, detailed=True, token_prices=token_prices
        )

        message = r"⚠️ *Close Position?*" + "\n\n"
        message += escape_markdown_v2(detail) + "\n\n"
        message += r"_This will remove all liquidity from this position\._"

        keyboard = [
            [
                InlineKeyboardButton(
                    text="✅ Yes, Close",
                    callback_data=f"dex:pos_close_exec:{pos_index}",
                ),
                InlineKeyboardButton(text="❌ Cancel", callback_data="dex:liquidity"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        # Try to edit the message, fallback to sending new message if it's a media message
        try:
            await update.callback_query.message.edit_text(
                message, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
        except Exception as edit_err:
            if "no text in the message" in str(edit_err).lower():
                # Message is likely a photo/media - send a new message instead
                await update.callback_query.message.answer(
                    message, parse_mode="MarkdownV2", reply_markup=reply_markup
                )
            else:
                raise

    except Exception as e:
        logger.error(f"Error showing close confirmation: {e}", exc_info=True)
        await update.callback_query.answer(f"Error: {str(e)[:100]}")


async def handle_pos_close_execute(
    update: Update, context: Context, pos_index: str
) -> None:
    """Execute closing a position (remove all liquidity)"""
    try:
        positions_cache = context.user_data.get("positions_cache", {})
        pos = positions_cache.get(pos_index)

        if not pos:
            await update.callback_query.answer("Position not found. Please refresh.")
            return

        # Get token cache and prices for display
        token_cache = context.user_data.get("token_cache") or {}
        token_prices = context.user_data.get("token_prices", {})
        detail = _format_position_detail(
            pos, token_cache=token_cache, detailed=True, token_prices=token_prices
        )

        # Immediately update message to show closing status (remove keyboard)
        closing_msg = r"⏳ *Closing Position\.\.\.*" + "\n\n"
        closing_msg += escape_markdown_v2(detail) + "\n\n"
        closing_msg += r"_Please wait, this may take a moment\._"

        await update.callback_query.answer()
        try:
            await update.callback_query.message.edit_text(
                closing_msg, parse_mode="MarkdownV2"
            )
        except Exception:
            await update.callback_query.message.answer(
                closing_msg, parse_mode="MarkdownV2"
            )

        chat_id = update.effective_chat.id
        client = await get_client(chat_id, user_data=context.user_data)

        if not hasattr(client, "gateway_clmm"):
            raise ValueError("Gateway CLMM not available")

        # Get position details
        connector = pos.get("connector", "meteora")
        network = pos.get("network", "solana-mainnet-beta")
        position_address = pos.get("position_address", pos.get("nft_id", ""))

        # Close the position completely
        result = await client.gateway_clmm.close_position(
            connector=connector, network=network, position_address=position_address
        )

        if result:
            # Remove this specific position from cache, but keep others (for LP monitor alerts)
            positions_cache = context.user_data.get("positions_cache", {})
            if pos_index in positions_cache:
                del positions_cache[pos_index]
            # Clear the full position list cache to force refresh on next view
            context.user_data.pop("all_positions", None)
            context.user_data.pop(
                "lp_positions_cache", None
            )  # Also clear LP list cache

            pair = pos.get("trading_pair", "Unknown")
            success_msg = escape_markdown_v2(f"✅ Position closed: {pair}")

            if isinstance(result, dict) and result.get("transaction_hash"):
                success_msg += f"\n\nTx: `{escape_markdown_v2(result['transaction_hash'][:20])}...`"

            keyboard = [
                [InlineKeyboardButton(text="« Back", callback_data="dex:liquidity")]
            ]
            reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

            try:
                await update.callback_query.message.edit_text(
                    success_msg, parse_mode="MarkdownV2", reply_markup=reply_markup
                )
            except Exception:
                await update.callback_query.message.answer(
                    success_msg, parse_mode="MarkdownV2", reply_markup=reply_markup
                )
        else:
            await update.callback_query.answer("Failed to close position")

    except Exception as e:
        logger.error(f"Error closing position: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to close position: {str(e)}")
        await update.callback_query.message.answer(
            error_message, parse_mode="MarkdownV2"
        )


async def handle_position_list(update: Update, context: Context) -> None:
    """Handle CLMM position list for specific pool"""
    help_text = (
        r"📍 *Get CLMM Positions*" + "\n\n"
        r"Reply with:" + "\n\n"
        r"`connector network pool_address`" + "\n\n"
        r"*Example:*" + "\n"
        r"`meteora solana\-mainnet\-beta POOL_ADDRESS`"
    )

    keyboard = [[InlineKeyboardButton(text="« Cancel", callback_data="dex:liquidity")]]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    context.user_data["dex_state"] = "position_list"

    await update.callback_query.message.answer(
        help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def process_position_list(
    update: Update, context: Context, user_input: str
) -> None:
    """Process position list"""
    try:
        parts = user_input.split()
        if len(parts) < 3:
            raise ValueError("Need: connector network pool_address")

        connector = parts[0]
        network = parts[1]
        pool_address = parts[2]

        chat_id = update.effective_chat.id
        client = await get_client(chat_id, user_data=context.user_data)

        if not hasattr(client, "gateway_clmm"):
            raise ValueError("Gateway CLMM not available")

        # positions-owned has no pool filter: it lists every CLMM position the
        # wallet owns on the connector, each row carrying its own pool_address.
        all_positions = await client.gateway_clmm.get_positions_owned(
            connector=connector, network=network
        )
        positions = [
            p for p in (all_positions or []) if p.get("pool_address") == pool_address
        ]

        # Save params
        set_dex_last_pool(
            context.user_data,
            {"connector": connector, "network": network, "pool_address": pool_address},
        )

        if not positions:
            message = escape_markdown_v2("📍 No positions found")
        else:
            pos_lines = []
            for pos in positions[:5]:
                pos_id = pos.get("position_address", pos.get("nft_id", "N/A"))
                lower = pos.get("lower_price", "N/A")
                upper = pos.get("upper_price", "N/A")
                pos_lines.append(f"• {pos_id[:8]}... [{lower}-{upper}]")

            pos_text = escape_markdown_v2("\n".join(pos_lines))
            message = (
                rf"📍 *CLMM Positions* \({len(positions)} found\)" + "\n\n" + pos_text
            )

        await update.message.answer(message, parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Error getting positions: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to get positions: {str(e)}")
        await update.message.answer(error_message, parse_mode="MarkdownV2")
