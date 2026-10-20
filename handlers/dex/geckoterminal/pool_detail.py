"""Pool detail view with chart or text layout."""

import logging

from condor.pool_data import (
    can_fetch_liquidity,
    gecko_call,
)
from condor.telegram.transport import as_input_file
from condor.telegram.types import (
    NO_LINK_PREVIEW,
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from utils.telegram_formatters import escape_markdown_v2

from .._shared import format_timeframe_label
from ..visualizations import (
    generate_ohlcv_chart,
)
from ._format import (
    NETWORK_NAMES,
    _extract_pool_data,
    _format_change,
    _format_price,
    _format_volume,
)

logger = logging.getLogger(__name__)


async def show_pool_detail(update: Update, context: Context, pool_index: int) -> None:
    """Show OHLCV chart automatically when selecting a pool with action buttons"""
    query = update.callback_query

    pools = context.user_data.get("gecko_pools", [])
    if pool_index >= len(pools):
        await query.answer("Pool not found")
        return

    pool = pools[pool_index]
    pool_data = _extract_pool_data(pool)

    # Store current pool for OHLCV/trades
    context.user_data["gecko_selected_pool"] = pool_data
    context.user_data["gecko_selected_pool_index"] = pool_index

    # Default timeframe for initial view
    default_timeframe = "1h"  # 1h candles showing 1 day of data

    # Show OHLCV chart automatically
    await _show_pool_chart(update, context, pool_data, default_timeframe)


async def _show_pool_chart(
    update: Update, context: Context, pool_data: dict, timeframe: str
) -> None:
    """Internal function to show pool OHLCV chart with action buttons"""
    query = update.callback_query

    await query.answer("Loading chart...")

    chat = query.message.chat

    # Show loading - delete current message and send loading text
    try:
        await query.message.delete()
    except Exception:
        pass  # Message may already be deleted

    loading_msg = await chat.send_message(
        f"📈 *{escape_markdown_v2(pool_data['name'])}*\n\n_Loading chart\\.\\.\\._",
        parse_mode="MarkdownV2",
    )

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
                logger.info(
                    f"OHLCV DataFrame columns: {list(result.columns)}, shape: {result.shape}"
                )
                if not result.empty:
                    if (
                        result.index.name == "datetime"
                        or "datetime" not in result.columns
                    ):
                        result = result.reset_index()
                    ohlcv_data = result.values.tolist()
                    logger.info(
                        f"Converted OHLCV DataFrame with {len(ohlcv_data)} rows"
                    )
        except ImportError:
            pass

        # If not a DataFrame, try other formats
        if not ohlcv_data:
            if isinstance(result, dict):
                ohlcv_data = (
                    result.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
                )
            elif hasattr(result, "data"):
                data = result.data
                if isinstance(data, dict):
                    ohlcv_data = data.get("attributes", {}).get("ohlcv_list", [])
                elif hasattr(data, "attributes"):
                    ohlcv_data = getattr(data.attributes, "ohlcv_list", [])
            elif isinstance(result, list):
                ohlcv_data = result

        if not ohlcv_data:
            # Fall back to text view if no chart data
            await _show_pool_text_detail(loading_msg, context, pool_data)
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
            await _show_pool_text_detail(loading_msg, context, pool_data)
            return

        # Build caption with detailed info
        pool_index = context.user_data.get("gecko_selected_pool_index", 0)
        dex_id = pool_data.get("dex_id", "")
        network = pool_data.get("network", "")
        network_name = NETWORK_NAMES.get(network, network)

        caption_lines = [f"📈 *{escape_markdown_v2(pool_data['name'])}*"]

        # Price line
        price_parts = []
        if pool_data.get("base_token_price_usd"):
            try:
                price = float(pool_data["base_token_price_usd"])
                price_parts.append(f"💰 {escape_markdown_v2(_format_price(price))}")
            except (ValueError, TypeError):
                pass

        change_24h = pool_data.get("price_change_24h")
        if change_24h is not None:
            try:
                change = float(change_24h)
                emoji = "🟢" if change >= 0 else "🔴"
                price_parts.append(
                    f"{emoji} {escape_markdown_v2(_format_change(change))} 24h"
                )
            except (ValueError, TypeError):
                pass

        if price_parts:
            caption_lines.append(" • ".join(price_parts))

        # Network/DEX line
        caption_lines.append(
            f"🌐 {escape_markdown_v2(network_name)} • 🏦 {escape_markdown_v2(dex_id)}"
        )

        # Price changes line
        changes = []
        for period, key in [
            ("1h", "price_change_1h"),
            ("6h", "price_change_6h"),
            ("24h", "price_change_24h"),
        ]:
            change = pool_data.get(key)
            if change is not None:
                try:
                    changes.append(f"{period}: {_format_change(float(change))}")
                except (ValueError, TypeError):
                    pass
        if changes:
            caption_lines.append(f"📊 {escape_markdown_v2(' | '.join(changes))}")

        # Volume line
        vols = []
        for period, key in [
            ("1h", "volume_1h"),
            ("6h", "volume_6h"),
            ("24h", "volume_24h"),
        ]:
            vol = pool_data.get(key)
            if vol:
                try:
                    vols.append(f"{period}: {_format_volume(float(vol))}")
                except (ValueError, TypeError):
                    pass
        if vols:
            caption_lines.append(f"📈 Vol {escape_markdown_v2(' | '.join(vols))}")

        # Market metrics line
        metrics = []
        if pool_data.get("reserve_usd"):
            try:
                metrics.append(
                    f"Liq: {_format_volume(float(pool_data['reserve_usd']))}"
                )
            except (ValueError, TypeError):
                pass
        if pool_data.get("fdv_usd"):
            try:
                metrics.append(f"FDV: {_format_volume(float(pool_data['fdv_usd']))}")
            except (ValueError, TypeError):
                pass
        if pool_data.get("market_cap_usd"):
            try:
                metrics.append(
                    f"MC: {_format_volume(float(pool_data['market_cap_usd']))}"
                )
            except (ValueError, TypeError):
                pass
        if metrics:
            caption_lines.append(f"💎 {escape_markdown_v2(' | '.join(metrics))}")

        # Transactions line
        txns = pool_data.get("transactions_24h", {})
        if txns:
            buys = txns.get("buys", 0)
            sells = txns.get("sells", 0)
            if buys or sells:
                caption_lines.append(f"🔄 24h Txns: {buys} buys / {sells} sells")

        caption = "\n".join(caption_lines)

        # Build keyboard - reorganized layout
        supports_liquidity = can_fetch_liquidity(dex_id, network)

        keyboard = [
            # Row 1: Timeframe
            [
                InlineKeyboardButton(
                    text="1h" if timeframe != "1m" else "• 1h •",
                    callback_data="dex:gecko_pool_tf:1m",
                ),
                InlineKeyboardButton(
                    text="1d" if timeframe != "1h" else "• 1d •",
                    callback_data="dex:gecko_pool_tf:1h",
                ),
                InlineKeyboardButton(
                    text="7d" if timeframe != "1d" else "• 7d •",
                    callback_data="dex:gecko_pool_tf:1d",
                ),
            ],
            # Row 2: Swap | LP
            [
                InlineKeyboardButton(text="💱 Swap", callback_data="dex:gecko_swap"),
                InlineKeyboardButton(
                    text="➕ Add LP", callback_data="dex:gecko_add_liquidity"
                ),
            ],
            # Row 3: Trades | Liquidity Distribution
            [
                InlineKeyboardButton(
                    text="📜 Trades", callback_data="dex:gecko_trades"
                ),
                InlineKeyboardButton(
                    text="📊 Liquidity", callback_data="dex:gecko_liquidity"
                ),
            ],
            # Row 4: Add to Gateway
            [
                InlineKeyboardButton(
                    text="🔗 Add to Gateway", callback_data="dex:gecko_add_tokens"
                ),
            ],
            # Row 5: Refresh | Back
            [
                InlineKeyboardButton(
                    text="🔄 Refresh", callback_data=f"dex:gecko_pool:{pool_index}"
                ),
                InlineKeyboardButton(
                    text="« Back", callback_data="dex:gecko_back_to_list"
                ),
            ],
        ]

        # Delete loading message and send photo
        try:
            await loading_msg.delete()
        except Exception:
            pass  # Message may already be deleted

        await chat.send_photo(
            photo=as_input_file(chart_buffer),
            caption=caption,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )

    except Exception as e:
        logger.error(f"Error generating pool chart: {e}", exc_info=True)
        # Fall back to text view on error
        try:
            await _show_pool_text_detail(loading_msg, context, pool_data)
        except Exception:
            # If loading_msg was deleted, send new error message
            await chat.send_message(
                f"❌ Error loading chart: {escape_markdown_v2(str(e))}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="« Back", callback_data="dex:gecko_back_to_list"
                            )
                        ]
                    ]
                ),
            )


async def _show_pool_text_detail(message, context: Context, pool_data: dict) -> None:
    """Show pool details as text (fallback when chart unavailable)"""
    pool_index = context.user_data.get("gecko_selected_pool_index", 0)
    network_name = NETWORK_NAMES.get(pool_data["network"], pool_data["network"])

    lines = [
        f"📊 *{escape_markdown_v2(pool_data['name'])}*\n",
        f"🌐 Network: {escape_markdown_v2(network_name)}",
        f"🏦 DEX: {escape_markdown_v2(pool_data['dex_id'])}",
        "",
        r"💰 *Price Info:*",
    ]

    if pool_data["base_token_price_usd"]:
        try:
            price = float(pool_data["base_token_price_usd"])
            symbol = escape_markdown_v2(pool_data["base_token_symbol"])
            lines.append(f"• {symbol}: {escape_markdown_v2(_format_price(price))}")
        except (ValueError, TypeError):
            pass

    if pool_data["quote_token_price_usd"]:
        try:
            price = float(pool_data["quote_token_price_usd"])
            symbol = escape_markdown_v2(pool_data["quote_token_symbol"])
            lines.append(f"• {symbol}: {escape_markdown_v2(_format_price(price))}")
        except (ValueError, TypeError):
            pass

    lines.append("")
    lines.append(r"📈 *Price Changes:*")

    for period, key in [
        ("1h", "price_change_1h"),
        ("6h", "price_change_6h"),
        ("24h", "price_change_24h"),
    ]:
        change = pool_data.get(key)
        if change is not None:
            try:
                change = float(change)
                lines.append(
                    f"• {period}: {escape_markdown_v2(_format_change(change))}"
                )
            except (ValueError, TypeError):
                pass

    lines.append("")
    lines.append(r"📊 *Volume:*")

    for period, key in [
        ("1h", "volume_1h"),
        ("6h", "volume_6h"),
        ("24h", "volume_24h"),
    ]:
        vol = pool_data.get(key)
        if vol:
            try:
                vol = float(vol)
                lines.append(f"• {period}: {escape_markdown_v2(_format_volume(vol))}")
            except (ValueError, TypeError):
                pass

    # Market cap and FDV
    lines.append("")
    if pool_data.get("market_cap_usd"):
        try:
            mc = float(pool_data["market_cap_usd"])
            lines.append(f"💎 Market Cap: {escape_markdown_v2(_format_volume(mc))}")
        except (ValueError, TypeError):
            pass

    if pool_data.get("fdv_usd"):
        try:
            fdv = float(pool_data["fdv_usd"])
            lines.append(f"📈 FDV: {escape_markdown_v2(_format_volume(fdv))}")
        except (ValueError, TypeError):
            pass

    if pool_data.get("reserve_usd"):
        try:
            reserve = float(pool_data["reserve_usd"])
            lines.append(f"💧 Liquidity: {escape_markdown_v2(_format_volume(reserve))}")
        except (ValueError, TypeError):
            pass

    # Pool address and link
    lines.append("")
    addr = pool_data.get("address", "")
    network = pool_data.get("network", "")
    if addr:
        lines.append(f"📍 Address:\n`{addr}`")
        if network:
            gecko_url = f"https://www.geckoterminal.com/{network}/pools/{addr}"
            lines.append(
                f"\n🦎 [View on GeckoTerminal]({escape_markdown_v2(gecko_url)})"
            )

    # Build keyboard
    dex_id = pool_data.get("dex_id", "")
    supports_liquidity = can_fetch_liquidity(dex_id, network)

    keyboard = [
        [
            InlineKeyboardButton(text="📈 Charts", callback_data="dex:gecko_charts"),
            InlineKeyboardButton(text="💱 Swap", callback_data="dex:gecko_swap"),
            InlineKeyboardButton(text="📜 Trades", callback_data="dex:gecko_trades"),
        ],
    ]

    if supports_liquidity:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="➕ Add Liquidity", callback_data="dex:gecko_add_liquidity"
                ),
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="🔄 Refresh", callback_data=f"dex:gecko_pool:{pool_index}"
            ),
            InlineKeyboardButton(text="« Back", callback_data="dex:gecko_back_to_list"),
        ]
    )

    await message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        link_preview_options=NO_LINK_PREVIEW,
    )


async def handle_gecko_pool_tf(
    update: Update, context: Context, timeframe: str
) -> None:
    """Handle timeframe switching in pool detail view - maintains action buttons"""
    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await update.callback_query.answer("No pool selected")
        return

    await _show_pool_chart(update, context, pool_data, timeframe)
