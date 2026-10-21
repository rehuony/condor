"""Charts sub-menu: OHLCV, liquidity and combined charts."""

import logging

from condor.pool_data import (
    can_fetch_liquidity,
    fetch_liquidity_bins,
    gecko_call,
    get_connector_for_dex,
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
from utils.telegram_formatters import escape_markdown_v2, format_error_message

from .._shared import format_timeframe_label
from ..visualizations import (
    generate_combined_chart,
    generate_liquidity_chart,
    generate_ohlcv_chart,
)
from ._format import _format_price

logger = logging.getLogger(__name__)


async def show_gecko_charts_menu(update: Update, context: Context) -> None:
    """Show charts sub-menu with all chart options (candles, liquidity, combined)"""
    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    await query.answer()

    dex_id = pool_data.get("dex_id", "")
    network = pool_data.get("network", "")
    pool_name = pool_data.get("name", "Pool")
    supports_liquidity = can_fetch_liquidity(dex_id, network)

    # Build charts menu
    lines = [
        f"📈 *Charts for {escape_markdown_v2(pool_name)}*",
        "",
        "Select a chart type:",
    ]

    keyboard = [
        # Candle timeframes row
        [
            InlineKeyboardButton(text="📈 1h", callback_data="dex:gecko_ohlcv:1m"),
            InlineKeyboardButton(text="📈 1d", callback_data="dex:gecko_ohlcv:1h"),
            InlineKeyboardButton(text="📈 7d", callback_data="dex:gecko_ohlcv:1d"),
        ],
    ]

    # Add liquidity and combined buttons only for supported DEXes
    if supports_liquidity:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="📊 Liquidity", callback_data="dex:gecko_liquidity"
                ),
            ]
        )
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="📊 Combined 1h", callback_data="dex:gecko_combined:1m"
                ),
                InlineKeyboardButton(
                    text="📊 Combined 1d", callback_data="dex:gecko_combined:1h"
                ),
            ]
        )

    # Back to pool detail
    pool_index = context.user_data.get("gecko_selected_pool_index", 0)
    keyboard.append(
        [
            InlineKeyboardButton(
                text="« Back to Pool", callback_data=f"dex:gecko_pool:{pool_index}"
            ),
        ]
    )

    # Handle photo messages - can't edit photo to text
    if getattr(query.message, "photo", None):
        await query.message.delete()
        await query.message.answer(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
    else:
        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )


async def show_ohlcv_chart(update: Update, context: Context, timeframe: str) -> None:
    """Fetch OHLCV data and display as chart"""
    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    await query.answer("Loading chart...")

    # Show loading - handle photo messages (can't edit photo to text)
    if getattr(query.message, "photo", None):
        await query.message.delete()
        loading_msg = await query.message.answer(
            f"📈 *OHLCV Chart*\n\n_Loading {timeframe} data\\.\\.\\._",
            parse_mode="MarkdownV2",
        )
    else:
        await query.message.edit_text(
            f"📈 *OHLCV Chart*\n\n_Loading {timeframe} data\\.\\.\\._",
            parse_mode="MarkdownV2",
        )
        loading_msg = query.message

    try:
        network = pool_data["network"]
        address = pool_data["address"]

        result = await gecko_call("get_ohlcv", network, address, timeframe)

        logger.info(f"OHLCV raw response type: {type(result)}")

        # Handle various response formats for OHLCV
        ohlcv_data = []

        # Check for pandas DataFrame first (library often returns DataFrames)
        try:
            import pandas as pd

            if isinstance(result, pd.DataFrame):
                # DataFrame columns: typically datetime, open, high, low, close, volume
                logger.info(
                    f"OHLCV DataFrame columns: {list(result.columns)}, shape: {result.shape}"
                )
                if not result.empty:
                    # Convert to list of lists, handling index if it's the timestamp
                    if (
                        result.index.name == "datetime"
                        or "datetime" not in result.columns
                    ):
                        # Index is the timestamp, reset it to include in data
                        result = result.reset_index()
                    ohlcv_data = result.values.tolist()
                    logger.info(
                        f"Converted OHLCV DataFrame with {len(ohlcv_data)} rows"
                    )
                    if ohlcv_data:
                        logger.debug(f"First OHLCV row: {ohlcv_data[0]}")
        except ImportError:
            pass

        # If not a DataFrame, try other formats
        if not ohlcv_data:
            if isinstance(result, dict):
                logger.debug(f"OHLCV dict keys: {result.keys() if result else 'empty'}")
                ohlcv_data = (
                    result.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
                )
            elif hasattr(result, "data"):
                data = result.data
                if isinstance(data, dict):
                    ohlcv_data = data.get("attributes", {}).get("ohlcv_list", [])
                elif hasattr(data, "attributes"):
                    ohlcv_data = getattr(data.attributes, "ohlcv_list", [])
            # Try as a list directly
            elif isinstance(result, list):
                ohlcv_data = result
                logger.info(f"OHLCV is a direct list with {len(ohlcv_data)} items")

        logger.info(
            f"OHLCV final: type={type(result)}, extracted {len(ohlcv_data)} candles"
        )

        if not ohlcv_data:
            await loading_msg.edit_text(
                "📈 *OHLCV Chart*\n\n_No data available for this timeframe_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="« Back", callback_data="dex:gecko_charts"
                            )
                        ]
                    ]
                ),
            )
            return

        # Generate chart image using visualization module
        pair_name = pool_data.get("name", "Pool")
        base_symbol = pool_data.get("base_token_symbol")
        quote_symbol = pool_data.get("quote_token_symbol")

        chart_buffer = generate_ohlcv_chart(
            ohlcv_data=ohlcv_data,
            pair_name=pair_name,
            timeframe=format_timeframe_label(timeframe),
            base_symbol=base_symbol,
            quote_symbol=quote_symbol,
        )

        if not chart_buffer:
            await loading_msg.edit_text(
                "📈 *OHLCV Chart*\n\n_Failed to generate chart_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="« Back", callback_data="dex:gecko_charts"
                            )
                        ]
                    ]
                ),
            )
            return

        # Build caption
        caption_lines = [
            f"📈 *{escape_markdown_v2(pool_data['name'])}*",
            f"Timeframe: {escape_markdown_v2(format_timeframe_label(timeframe))}",
            f"Data points: {len(ohlcv_data)}",
        ]

        # Get latest price info
        if ohlcv_data:
            latest = ohlcv_data[0]  # Most recent candle
            if len(latest) >= 5:
                timestamp, open_p, high_p, low_p, close_p = latest[:5]
                caption_lines.append("")
                caption_lines.append(
                    f"Latest: {escape_markdown_v2(_format_price(close_p))}"
                )
                caption_lines.append(
                    f"High: {escape_markdown_v2(_format_price(high_p))}"
                )
                caption_lines.append(f"Low: {escape_markdown_v2(_format_price(low_p))}")

        caption = "\n".join(caption_lines)

        # Build keyboard - add combined view if supported DEX
        dex_id = pool_data.get("dex_id", "")
        network = pool_data.get("network", "")

        keyboard = [
            [
                InlineKeyboardButton(
                    text="1h" if timeframe != "1m" else "• 1h •",
                    callback_data="dex:gecko_ohlcv:1m",
                ),
                InlineKeyboardButton(
                    text="1d" if timeframe != "1h" else "• 1d •",
                    callback_data="dex:gecko_ohlcv:1h",
                ),
                InlineKeyboardButton(
                    text="7d" if timeframe != "1d" else "• 7d •",
                    callback_data="dex:gecko_ohlcv:1d",
                ),
            ],
        ]

        if can_fetch_liquidity(dex_id, network):
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="📊 + Liquidity",
                        callback_data=f"dex:gecko_combined:{timeframe}",
                    ),
                ]
            )

        keyboard.append(
            [
                InlineKeyboardButton(text="« Back", callback_data="dex:gecko_charts"),
            ]
        )

        # Delete loading message and send photo
        await loading_msg.delete()
        await loading_msg.answer_photo(
            photo=as_input_file(chart_buffer),
            caption=caption,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )

    except Exception as e:
        logger.error(f"Error generating OHLCV chart: {e}", exc_info=True)
        await loading_msg.edit_text(
            ui.notice("error", "Could not load the chart", str(e)),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="« Back", callback_data="dex:gecko_charts"
                        )
                    ]
                ]
            ),
        )


async def show_gecko_liquidity(update: Update, context: Context) -> None:
    """Show liquidity distribution chart for selected pool (Meteora/Raydium/Orca only)"""
    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    dex_id = pool_data.get("dex_id", "")
    network = pool_data.get("network", "")

    if not can_fetch_liquidity(dex_id, network):
        await query.answer(f"Liquidity data not available for {dex_id}")
        return

    await query.answer("Loading liquidity chart...")

    # Show loading
    if getattr(query.message, "photo", None):
        await query.message.delete()
        loading_msg = await query.message.answer(
            f"📊 *Liquidity Distribution*\n\n_Loading\\.\\.\\._",
            parse_mode="MarkdownV2",
        )
    else:
        await query.message.edit_text(
            f"📊 *Liquidity Distribution*\n\n_Loading\\.\\.\\._",
            parse_mode="MarkdownV2",
        )
        loading_msg = query.message

    try:
        address = pool_data["address"]
        connector = get_connector_for_dex(dex_id)

        # Fetch liquidity bins via gateway
        chat_id = update.effective_chat.id
        bins, pool_info, error = await fetch_liquidity_bins(
            pool_address=address,
            connector=connector,
            client=await get_client(chat_id, user_data=context.user_data),
        )

        if error or not bins:
            await loading_msg.edit_text(
                f"📊 *Liquidity Distribution*\n\n_No liquidity data available_\n\n{escape_markdown_v2(error or 'No bins found')}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="« Back", callback_data="dex:gecko_charts"
                            )
                        ]
                    ]
                ),
            )
            return

        # Get current price
        current_price = None
        if pool_info:
            current_price = pool_info.get("price") or pool_info.get("current_price")
            if current_price:
                current_price = float(current_price)

        # Generate chart
        pair_name = pool_data.get("name", "Pool")
        chart_bytes = generate_liquidity_chart(
            bins=bins, current_price=current_price, pair_name=pair_name
        )

        if not chart_bytes:
            await loading_msg.edit_text(
                "📊 *Liquidity Distribution*\n\n_Failed to generate chart_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="« Back", callback_data="dex:gecko_charts"
                            )
                        ]
                    ]
                ),
            )
            return

        # Build caption
        caption = f"📊 *{escape_markdown_v2(pair_name)}* \\- Liquidity Distribution\n"
        caption += f"_{escape_markdown_v2(f'{len(bins)} bins')}_"

        # Build keyboard
        keyboard = [
            [
                InlineKeyboardButton(
                    text="📊 Combined 1h", callback_data="dex:gecko_combined:1m"
                ),
                InlineKeyboardButton(
                    text="📊 Combined 1d", callback_data="dex:gecko_combined:1h"
                ),
            ],
            [
                InlineKeyboardButton(text="« Back", callback_data="dex:gecko_charts"),
            ],
        ]

        # Delete loading and send photo
        await loading_msg.delete()
        await loading_msg.answer_photo(
            photo=as_input_file(chart_bytes),
            caption=caption,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )

    except Exception as e:
        logger.error(f"Error generating liquidity chart: {e}", exc_info=True)
        await loading_msg.edit_text(
            format_error_message(str(e)),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="« Back", callback_data="dex:gecko_charts"
                        )
                    ]
                ]
            ),
        )


async def show_gecko_combined(update: Update, context: Context, timeframe: str) -> None:
    """Show combined OHLCV + Liquidity chart for selected pool"""
    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    dex_id = pool_data.get("dex_id", "")
    network = pool_data.get("network", "")

    if not can_fetch_liquidity(dex_id, network):
        await query.answer(f"Combined view not available for {dex_id}")
        return

    await query.answer("Loading combined chart...")

    # Show loading - keep the message reference for editing
    loading_msg = query.message
    if getattr(query.message, "photo", None):
        # Edit photo caption to show loading (keeps the existing photo)
        try:
            await query.message.edit_caption(
                caption=f"📊 *Combined View*\n\n_Loading OHLCV \\+ Liquidity\\.\\.\\._",
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass  # Caption might be the same, ignore
    else:
        await query.message.edit_text(
            f"📊 *Combined View*\n\n_Loading OHLCV \\+ Liquidity\\.\\.\\._",
            parse_mode="MarkdownV2",
        )

    try:
        address = pool_data["address"]
        connector = get_connector_for_dex(dex_id)

        # Fetch OHLCV data
        ohlcv_result = await gecko_call("get_ohlcv", network, address, timeframe)

        # Parse OHLCV response
        ohlcv_data = []
        try:
            import pandas as pd

            if isinstance(ohlcv_result, pd.DataFrame) and not ohlcv_result.empty:
                if (
                    ohlcv_result.index.name == "datetime"
                    or "datetime" not in ohlcv_result.columns
                ):
                    ohlcv_result = ohlcv_result.reset_index()
                ohlcv_data = ohlcv_result.values.tolist()
        except ImportError:
            pass

        if not ohlcv_data:
            if isinstance(ohlcv_result, list):
                ohlcv_data = ohlcv_result
            elif isinstance(ohlcv_result, dict):
                ohlcv_data = (
                    ohlcv_result.get("data", {})
                    .get("attributes", {})
                    .get("ohlcv_list", [])
                )

        # Fetch liquidity bins
        chat_id = update.effective_chat.id
        bins, pool_info, _ = await fetch_liquidity_bins(
            pool_address=address,
            connector=connector,
            client=await get_client(chat_id, user_data=context.user_data),
        )

        if not ohlcv_data and not bins:
            await loading_msg.edit_text(
                "📊 *Combined View*\n\n_No data available_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="« Back", callback_data="dex:gecko_charts"
                            )
                        ]
                    ]
                ),
            )
            return

        # Get current price
        current_price = None
        if pool_info:
            current_price = pool_info.get("price") or pool_info.get("current_price")
            if current_price:
                current_price = float(current_price)

        # Generate combined chart
        pair_name = pool_data.get("name", "Pool")
        base_symbol = pool_data.get("base_token_symbol")
        quote_symbol = pool_data.get("quote_token_symbol")

        chart_buf = generate_combined_chart(
            ohlcv_data=ohlcv_data or [],
            bins=bins or [],
            pair_name=pair_name,
            timeframe=format_timeframe_label(timeframe),
            current_price=current_price,
            base_symbol=base_symbol,
            quote_symbol=quote_symbol,
        )

        if not chart_buf:
            await loading_msg.edit_text(
                "📊 *Combined View*\n\n_Failed to generate chart_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="« Back", callback_data="dex:gecko_charts"
                            )
                        ]
                    ]
                ),
            )
            return

        # Build caption
        caption = f"📊 *{escape_markdown_v2(pair_name)}* \\- Combined View\n"
        caption += f"_OHLCV \\({escape_markdown_v2(format_timeframe_label(timeframe))}\\) \\+ Liquidity_"

        # Build keyboard
        keyboard = [
            [
                InlineKeyboardButton(
                    text="1h" if timeframe != "1m" else "• 1h •",
                    callback_data="dex:gecko_combined:1m",
                ),
                InlineKeyboardButton(
                    text="1d" if timeframe != "1h" else "• 1d •",
                    callback_data="dex:gecko_combined:1h",
                ),
                InlineKeyboardButton(
                    text="7d" if timeframe != "1d" else "• 7d •",
                    callback_data="dex:gecko_combined:1d",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📈 Candles Only", callback_data=f"dex:gecko_ohlcv:{timeframe}"
                ),
                InlineKeyboardButton(
                    text="📊 Liquidity Only", callback_data="dex:gecko_liquidity"
                ),
            ],
            [
                InlineKeyboardButton(text="« Back", callback_data="dex:gecko_charts"),
            ],
        ]

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
            await loading_msg.answer_photo(
                photo=as_input_file(chart_buf),
                caption=caption,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )

    except Exception as e:
        logger.error(f"Error generating combined chart: {e}", exc_info=True)
        await loading_msg.edit_text(
            format_error_message(str(e)),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="« Back", callback_data="dex:gecko_charts"
                        )
                    ]
                ]
            ),
        )
