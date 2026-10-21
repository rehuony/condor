"""Liquidity, OHLCV and combined pool charts."""

import logging

from condor.pool_data import (
    fetch_liquidity_bins,
    fetch_ohlcv,
)
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
    format_error_message,
    resolve_token_symbol,
)

from .._shared import format_timeframe_label
from ..visualizations import (
    generate_aggregated_liquidity_chart,
    generate_combined_chart,
    generate_ohlcv_chart,
)
from ._common import _fetch_pool_info, _require_network

logger = logging.getLogger(__name__)


async def handle_plot_liquidity(
    update: Update, context: Context, percentile: int = 90
) -> None:
    """Handle Plot Liquidity button - aggregates liquidity from top pools by TVL

    Args:
        update: Telegram update
        context: Bot context
        percentile: TVL percentile threshold (e.g., 90 means top 90% by TVL)
    """
    import asyncio
    from io import BytesIO

    query = update.callback_query

    # Get cached pools
    cached_pools = context.user_data.get("pool_list_cache", [])
    search_term = context.user_data.get("pool_list_search_term", "")

    if not cached_pools:
        await query.answer("No pools cached. Please search again.", show_alert=True)
        return

    # Sort pools by TVL descending
    pools_by_tvl = sorted(
        cached_pools, key=lambda x: float(x.get("liquidity", 0) or 0), reverse=True
    )

    # Calculate TVL threshold for percentile
    total_tvl = sum(float(p.get("liquidity", 0) or 0) for p in pools_by_tvl)
    if total_tvl == 0:
        await query.answer("No pools with liquidity found.", show_alert=True)
        return

    # Select pools that make up the top percentile by TVL
    target_tvl = total_tvl * (percentile / 100)
    accumulated_tvl = 0
    selected_pools = []

    for pool in pools_by_tvl:
        pool_tvl = float(pool.get("liquidity", 0) or 0)
        if pool_tvl > 0:
            selected_pools.append(pool)
            accumulated_tvl += pool_tvl
            if accumulated_tvl >= target_tvl:
                break

    if not selected_pools:
        await query.answer("No pools selected for aggregation.", show_alert=True)
        return

    # Edit original message to show loading status
    await query.message.edit_text(
        "⏳ "
        + escape_markdown_v2(
            f"Fetching liquidity data from {len(selected_pools)} pools…"
        ),
        parse_mode="MarkdownV2",
    )

    try:
        chat_id = update.effective_chat.id
        client = await get_client(chat_id, user_data=context.user_data)

        # Fetch all pool infos in parallel with individual timeouts
        POOL_FETCH_TIMEOUT = 10  # seconds per pool

        async def fetch_pool_with_info(pool):
            """Fetch pool info and return combined data"""
            pool_address = pool.get("pool_address", pool.get("address", ""))
            connector = pool.get("connector", "meteora")
            try:
                pool_info = await asyncio.wait_for(
                    _fetch_pool_info(
                        client,
                        pool_address,
                        connector,
                        _require_network(pool, "This pool"),
                    ),
                    timeout=POOL_FETCH_TIMEOUT,
                )
                bins = pool_info.get("bins", [])
                bin_step = pool.get("bin_step") or pool_info.get("bin_step")

                # Log what we're getting
                logger.info(
                    f"Pool {pool_address[:8]}... bins={len(bins)}, bin_step={bin_step}"
                )
                if bins:
                    # Log first bin structure for debugging
                    logger.debug(f"First bin sample: {bins[0]}")

                return {
                    "pool": pool,
                    "pool_info": pool_info,
                    "bins": bins,
                    "bin_step": bin_step,
                }
            except asyncio.TimeoutError:
                logger.warning(
                    f"Timeout fetching pool {pool_address[:12]}... after {POOL_FETCH_TIMEOUT}s"
                )
                return None
            except Exception as e:
                logger.warning(f"Failed to fetch pool {pool_address}: {e}")
                return None

        # Fetch all pools simultaneously
        tasks = [fetch_pool_with_info(pool) for pool in selected_pools]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter successful results and count failures
        pools_data = [
            r for r in results if r is not None and not isinstance(r, Exception)
        ]
        failed_count = len(selected_pools) - len(pools_data)

        if not pools_data:
            await query.message.edit_text(
                format_error_message(
                    f"Failed to fetch pool data. All {len(selected_pools)} pools failed or timed out."
                ),
                parse_mode="MarkdownV2",
            )
            return

        # Log summary of what we got
        total_bins = sum(len(p.get("bins", [])) for p in pools_data)
        logger.info(f"Fetched {len(pools_data)} pools with total {total_bins} bins")

        # Check if we have any bins at all
        if total_bins == 0:
            await query.message.edit_text(
                format_error_message(
                    f"No liquidity bin data available for the {len(pools_data)} pools. The pools may not have detailed bin data exposed via the API."
                ),
                parse_mode="MarkdownV2",
            )
            return

        # Update loading message
        await query.message.edit_text(
            "⏳ "
            + escape_markdown_v2(
                f"Generating aggregated chart for {len(pools_data)} pools ({total_bins} bins)…"
            ),
            parse_mode="MarkdownV2",
        )

        # Generate aggregated chart
        pair_name = search_term if search_term else "Multi-Pool"
        chart_bytes = generate_aggregated_liquidity_chart(pools_data, pair_name)

        if not chart_bytes:
            # Try to give more info about what went wrong
            bins_with_liquidity = 0
            for pd in pools_data:
                for b in pd.get("bins", []):
                    base = float(b.get("base_token_amount", 0) or 0)
                    quote = float(b.get("quote_token_amount", 0) or 0)
                    if base > 0 or quote > 0:
                        bins_with_liquidity += 1

            error_detail = (
                f"Total bins: {total_bins}, bins with liquidity: {bins_with_liquidity}"
            )
            logger.error(f"Failed to generate aggregated chart. {error_detail}")

            await query.message.edit_text(
                escape_markdown_v2(
                    f"❌ Failed to generate aggregated liquidity chart.\n{error_detail}"
                ),
                parse_mode="MarkdownV2",
            )
            return

        # Delete the original message before sending the photo
        try:
            await query.message.delete()
        except Exception:
            pass

        # Build summary message
        total_tvl_selected = sum(
            float(p["pool"].get("liquidity", 0) or 0) for p in pools_data
        )
        bin_steps = [p.get("bin_step", 0) for p in pools_data if p.get("bin_step")]
        min_bin_step = min(bin_steps) if bin_steps else "N/A"

        lines = [
            f"📊 Aggregated Liquidity: {pair_name}",
            "",
            f"📈 Pools included: {len(pools_data)}"
            + (f" ({failed_count} failed)" if failed_count else ""),
            f"💰 Total TVL: ${format_compact_number(total_tvl_selected)}",
            f"📊 Percentile: Top {percentile}%",
            f"🎯 Min bin step (resolution): {min_bin_step}",
        ]

        message = escape_markdown_v2("\n".join(lines))

        # Build keyboard
        keyboard = [
            [
                InlineKeyboardButton(
                    text="« Back to List", callback_data="dex:pool_list_back"
                ),
                InlineKeyboardButton(
                    text="« Back to LP Menu", callback_data="dex:liquidity"
                ),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        # Send chart
        photo_file = BytesIO(chart_bytes)
        photo_file.name = "aggregated_liquidity.png"

        await query.message.answer_photo(
            photo=as_input_file(photo_file),
            caption=message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup,
        )

    except Exception as e:
        logger.error(f"Error in plot_liquidity: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to plot liquidity: {str(e)}")
        await query.message.edit_text(error_message, parse_mode="MarkdownV2")


async def handle_pool_ohlcv(
    update: Update,
    context: Context,
    timeframe: str,
    currency: str = "usd",
) -> None:
    """Show OHLCV chart for the selected pool using GeckoTerminal

    Args:
        update: Telegram update
        context: Bot context
        timeframe: OHLCV timeframe (1m, 5m, 15m, 1h, 4h, 1d)
        currency: Price currency - "usd" or "token" (quote token)
    """
    from condor.telegram.types import InputMediaPhoto

    query = update.callback_query
    await query.answer("Loading chart...")

    pool = context.user_data.get("selected_pool", {})
    pool_info = context.user_data.get("selected_pool_info", {})

    if not pool:
        await query.message.answer(
            ui.notice("warning", "No pool selected", "Select a pool first."),
            parse_mode="MarkdownV2",
        )
        return

    pool_address = pool.get("pool_address", pool.get("address", ""))
    pair = pool.get("trading_pair") or pool.get("name", "Pool")

    # Get quote token symbol for display
    quote_token = pool.get("quote_token", pool.get("token_b", ""))
    quote_symbol = pool.get("quote_symbol", "")
    if not quote_symbol and quote_token:
        quote_symbol = resolve_token_symbol(
            quote_token, context.user_data.get("token_cache", {})
        )
    if not quote_symbol:
        quote_symbol = "Quote"

    # Get network - default to Solana for CLMM pools
    network = pool.get("network", "solana")

    # Show loading in caption if photo, otherwise edit text
    msg = query.message
    try:
        if msg.photo:
            await msg.edit_caption(
                caption=escape_markdown_v2(f"📈 Loading {pair} chart..."),
                parse_mode="MarkdownV2",
            )
        else:
            await msg.edit_text(
                escape_markdown_v2(f"📈 Loading {pair} chart..."),
                parse_mode="MarkdownV2",
            )
    except Exception:
        pass

    try:
        # Fetch OHLCV data via GeckoTerminal
        ohlcv_data, error = await fetch_ohlcv(
            pool_address=pool_address,
            network=network,
            timeframe=timeframe,
            currency="token",
        )

        if error or not ohlcv_data:
            error_msg = escape_markdown_v2(
                f"❌ Failed to load OHLCV: {error or 'No data'}"
            )
            keyboard = [
                [
                    InlineKeyboardButton(
                        text="« Back to Pool", callback_data="dex:pool_detail_refresh"
                    )
                ]
            ]
            if msg.photo:
                await msg.edit_caption(
                    caption=error_msg,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                )
            else:
                await msg.edit_text(
                    error_msg,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                )
            return

        # Generate chart
        chart_buf = generate_ohlcv_chart(
            ohlcv_data=ohlcv_data,
            pair_name=pair,
            timeframe=format_timeframe_label(timeframe),
        )

        if not chart_buf:
            error_msg = escape_markdown_v2("❌ Failed to generate chart")
            keyboard = [
                [
                    InlineKeyboardButton(
                        text="« Back to Pool", callback_data="dex:pool_detail_refresh"
                    )
                ]
            ]
            if msg.photo:
                await msg.edit_caption(
                    caption=error_msg,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                )
            else:
                await msg.edit_text(
                    error_msg,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                )
            return

        # Toggle currency for button
        other_currency = "token" if currency == "usd" else "usd"
        currency_label = "USD" if currency == "usd" else quote_symbol
        toggle_label = quote_symbol if currency == "usd" else "USD"

        # Build timeframe buttons
        keyboard = [
            [
                InlineKeyboardButton(
                    text="1h" if timeframe != "1m" else "• 1h •",
                    callback_data=f"dex:pool_ohlcv:1m:{currency}",
                ),
                InlineKeyboardButton(
                    text="1d" if timeframe != "1h" else "• 1d •",
                    callback_data=f"dex:pool_ohlcv:1h:{currency}",
                ),
                InlineKeyboardButton(
                    text="7d" if timeframe != "1d" else "• 7d •",
                    callback_data=f"dex:pool_ohlcv:1d:{currency}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"💱 {toggle_label}",
                    callback_data=f"dex:pool_ohlcv:{timeframe}:{other_currency}",
                ),
                InlineKeyboardButton(
                    text="📊 + Liquidity",
                    callback_data=f"dex:pool_combined:{timeframe}:{currency}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="« Back to Pool", callback_data="dex:pool_detail_refresh"
                ),
            ],
        ]

        # Build caption
        caption = f"📈 *{escape_markdown_v2(pair)}* \\- {escape_markdown_v2(format_timeframe_label(timeframe))}\n"
        caption += f"_Price in {escape_markdown_v2(currency_label)} \\({escape_markdown_v2(f'{len(ohlcv_data)} candles')}\\)_"

        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        # Edit existing message with new photo
        if msg.photo:
            await msg.edit_media(
                media=InputMediaPhoto(
                    media=as_input_file(chart_buf),
                    caption=caption,
                    parse_mode="MarkdownV2",
                ),
                reply_markup=reply_markup,
            )
        else:
            # Delete text message and send photo
            await msg.delete()
            await query.message.answer_photo(
                photo=as_input_file(chart_buf),
                caption=caption,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )

    except Exception as e:
        logger.error(f"Error generating OHLCV chart: {e}", exc_info=True)
        error_msg = format_error_message(str(e)[:100])
        keyboard = [
            [
                InlineKeyboardButton(
                    text="« Back to Pool", callback_data="dex:pool_detail_refresh"
                )
            ]
        ]
        try:
            if msg.photo:
                await msg.edit_caption(
                    caption=error_msg,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                )
            else:
                await msg.edit_text(
                    error_msg,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                )
        except Exception:
            pass


async def handle_pool_combined_chart(
    update: Update,
    context: Context,
    timeframe: str,
    currency: str = "usd",
) -> None:
    """Show combined OHLCV + Liquidity chart for the selected pool

    Args:
        update: Telegram update
        context: Bot context
        timeframe: OHLCV timeframe
        currency: Price currency - "usd" or "token" (quote token)
    """

    query = update.callback_query
    await query.answer("Loading combined chart...")

    pool = context.user_data.get("selected_pool", {})
    pool_info = context.user_data.get("selected_pool_info", {})

    if not pool:
        await query.message.answer(
            ui.notice("warning", "No pool selected", "Select a pool first."),
            parse_mode="MarkdownV2",
        )
        return

    pool_address = pool.get("pool_address", pool.get("address", ""))
    pair = pool.get("trading_pair") or pool.get("name", "Pool")
    connector = pool.get("connector", "meteora")
    network = pool.get("network", "solana")
    current_price = pool_info.get("price") or pool.get("current_price")

    # Get quote token symbol for display
    quote_token = pool.get("quote_token", pool.get("token_b", ""))
    quote_symbol = pool.get("quote_symbol", "")
    if not quote_symbol and quote_token:
        quote_symbol = resolve_token_symbol(
            quote_token, context.user_data.get("token_cache", {})
        )
    if not quote_symbol:
        quote_symbol = "Quote"

    # Show loading - keep the message reference for editing
    loading_msg = query.message
    if getattr(query.message, "photo", None):
        # Edit photo caption to show loading
        try:
            await query.message.edit_caption(
                caption=f"📊 Loading combined chart for {escape_markdown_v2(pair)}\\.\\.\\.",
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass
    else:
        await query.message.edit_text(
            f"📊 Loading combined chart for {escape_markdown_v2(pair)}\\.\\.\\.",
            parse_mode="MarkdownV2",
        )

    try:
        # Fetch OHLCV data
        ohlcv_data, ohlcv_error = await fetch_ohlcv(
            pool_address=pool_address,
            network=network,
            timeframe=timeframe,
            currency="token",
        )

        # Get bins from cached pool_info or fetch
        bins = pool_info.get("bins", [])
        if not bins:
            chat_id = update.effective_chat.id
            bins, _, _ = await fetch_liquidity_bins(
                pool_address=pool_address,
                connector=connector,
                client=await get_client(chat_id, user_data=context.user_data),
            )

        if not ohlcv_data and not bins:
            await loading_msg.edit_text(
                format_error_message("No data available for this pool"),
                parse_mode="MarkdownV2",
            )
            return

        # Get lower/upper prices from add_position_params if available
        params = context.user_data.get("add_position_params", {})
        lower_price = None
        upper_price = None
        try:
            if params.get("lower_price"):
                lower_price = float(params["lower_price"])
            if params.get("upper_price"):
                upper_price = float(params["upper_price"])
        except (ValueError, TypeError):
            pass

        # Generate combined chart
        chart_buf = generate_combined_chart(
            ohlcv_data=ohlcv_data or [],
            bins=bins or [],
            pair_name=pair,
            timeframe=format_timeframe_label(timeframe),
            current_price=float(current_price) if current_price else None,
            lower_price=lower_price,
            upper_price=upper_price,
        )

        if not chart_buf:
            await loading_msg.edit_text(
                format_error_message("Failed to generate combined chart"),
                parse_mode="MarkdownV2",
            )
            return

        # Toggle currency for button
        other_currency = "token" if currency == "usd" else "usd"
        currency_label = "USD" if currency == "usd" else quote_symbol
        toggle_label = quote_symbol if currency == "usd" else "USD"

        # Build keyboard
        keyboard = [
            [
                InlineKeyboardButton(
                    text="1h" if timeframe != "1m" else "• 1h •",
                    callback_data=f"dex:pool_combined:1m:{currency}",
                ),
                InlineKeyboardButton(
                    text="1d" if timeframe != "1h" else "• 1d •",
                    callback_data=f"dex:pool_combined:1h:{currency}",
                ),
                InlineKeyboardButton(
                    text="7d" if timeframe != "1d" else "• 7d •",
                    callback_data=f"dex:pool_combined:1d:{currency}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"💱 {toggle_label}",
                    callback_data=f"dex:pool_combined:{timeframe}:{other_currency}",
                ),
                InlineKeyboardButton(
                    text="📈 OHLCV Only",
                    callback_data=f"dex:pool_ohlcv:{timeframe}:{currency}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="« Back to Pool", callback_data="dex:pool_detail_refresh"
                ),
            ],
        ]

        # Build caption
        caption = f"📊 *{escape_markdown_v2(pair)}* \\- Combined View\n"
        caption += f"_OHLCV in {escape_markdown_v2(currency_label)} \\({escape_markdown_v2(format_timeframe_label(timeframe))}\\) \\+ Liquidity_"

        # Edit or send photo
        from condor.telegram.types import InputMediaPhoto

        if loading_msg.photo:
            # Edit existing photo message
            await loading_msg.edit_media(
                media=InputMediaPhoto(
                    media=as_input_file(chart_buf),
                    caption=caption,
                    parse_mode="MarkdownV2",
                ),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )
        else:
            # Replace text with photo
            await loading_msg.delete()
            await query.message.answer_photo(
                photo=as_input_file(chart_buf),
                caption=caption,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )

    except Exception as e:
        logger.error(f"Error generating combined chart: {e}", exc_info=True)
        await loading_msg.edit_text(
            format_error_message(str(e)), parse_mode="MarkdownV2"
        )
