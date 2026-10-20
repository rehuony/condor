"""Recent trades for a pool."""

import logging
from datetime import datetime

from condor.pool_data import (
    gecko_call,
)
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from utils.telegram_formatters import escape_markdown_v2

from ._format import _extract_pools_from_response, _format_volume

logger = logging.getLogger(__name__)


async def show_recent_trades(update: Update, context: Context) -> None:
    """Show recent trades for selected pool"""
    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    await query.answer("Loading trades...")

    chat = query.message.chat

    # Show loading - delete current message (may be photo) and send text
    try:
        await query.message.delete()
    except Exception:
        pass

    loading_msg = await chat.send_message(
        r"📜 *Recent Trades*" + "\n\n" + r"_Loading\.\.\._", parse_mode="MarkdownV2"
    )

    try:
        network = pool_data["network"]
        address = pool_data["address"]

        result = await gecko_call("get_trades", network, address, 20)

        logger.info(f"Trades raw response type: {type(result)}")

        # Handle DataFrame response
        trades = []
        try:
            import pandas as pd

            if isinstance(result, pd.DataFrame):
                logger.info(f"Trades DataFrame columns: {list(result.columns)}")
                if not result.empty:
                    trades = result.to_dict("records")
                    if trades:
                        logger.debug(f"First trade keys: {list(trades[0].keys())}")
        except ImportError:
            pass

        if not trades:
            trades = _extract_pools_from_response(result, 20)

        if not trades:
            await loading_msg.edit_text(
                r"📜 *Recent Trades*" + "\n\n" + "_No recent trades found_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="« Back",
                                callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}",
                            )
                        ]
                    ]
                ),
            )
            return

        # Build trades message
        pair = f"{pool_data['base_token_symbol']}/{pool_data['quote_token_symbol']}"
        lines = [f"📜 *Recent Trades \\- {escape_markdown_v2(pair)}*\n"]

        lines.append("```")
        lines.append(f"{'Type':<6} {'Amount':<12} {'Price':<14} {'Time':<10}")
        lines.append("-" * 44)

        for trade in trades[:15]:
            attrs = trade.get("attributes", trade)

            # Get trade type - 'side' contains buy/sell, 'type' is always "trade"
            trade_type = (
                attrs.get("side") or attrs.get("kind") or attrs.get("trade_type") or "?"
            )
            if isinstance(trade_type, str):
                trade_type = trade_type[:4]  # "buy" or "sell"
            else:
                trade_type = "?"

            # Get amount - 'volume_usd' is the correct field from the API
            amount = (
                attrs.get("volume_usd")
                or attrs.get("volume_in_usd")
                or attrs.get("amount_usd")
            )
            if amount:
                try:
                    amount = float(amount)
                    amount_str = _format_volume(amount).replace("$", "")[:11]
                except (ValueError, TypeError):
                    amount_str = "?"
            else:
                amount_str = "?"

            # Get price - show base token price based on trade side
            # buy: swapping quote→base, price_to = base price
            # sell: swapping base→quote, price_from = base price
            if trade_type.lower() == "buy":
                price = attrs.get("price_to_in_usd")
            else:
                price = attrs.get("price_from_in_usd")

            # Fallback to any available price
            if not price:
                price = attrs.get("price_to_in_usd") or attrs.get("price_from_in_usd")

            if price:
                try:
                    price = float(price)
                    price_str = f"${price:.6f}"[:13]
                except (ValueError, TypeError):
                    price_str = "?"
            else:
                price_str = "?"

            # Get time - try multiple field names
            timestamp = (
                attrs.get("block_timestamp")
                or attrs.get("timestamp")
                or attrs.get("datetime")
                or attrs.get("time")
            )
            if timestamp:
                try:
                    # Handle pandas Timestamp
                    if hasattr(timestamp, "strftime"):
                        time_str = timestamp.strftime("%H:%M:%S")
                    elif isinstance(timestamp, (int, float)):
                        dt = datetime.fromtimestamp(timestamp)
                        time_str = dt.strftime("%H:%M:%S")
                    else:
                        dt = datetime.fromisoformat(
                            str(timestamp).replace("Z", "+00:00")
                        )
                        time_str = dt.strftime("%H:%M:%S")
                except Exception:
                    time_str = "?"
            else:
                time_str = "?"

            emoji = "🟢" if str(trade_type).lower() == "buy" else "🔴"
            lines.append(
                f"{emoji}{trade_type:<5} {amount_str:<12} {price_str:<14} {time_str:<10}"
            )

        lines.append("```")

        keyboard = [
            [
                InlineKeyboardButton(
                    text="🔄 Refresh", callback_data="dex:gecko_trades"
                ),
                InlineKeyboardButton(
                    text="« Back",
                    callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}",
                ),
            ],
        ]

        await loading_msg.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )

    except Exception as e:
        logger.error(f"Error fetching trades: {e}", exc_info=True)
        try:
            await loading_msg.edit_text(
                f"❌ Error loading trades: {escape_markdown_v2(str(e))}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="« Back",
                                callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}",
                            )
                        ]
                    ]
                ),
            )
        except Exception:
            await chat.send_message(
                f"❌ Error loading trades: {escape_markdown_v2(str(e))}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="« Back",
                                callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}",
                            )
                        ]
                    ]
                ),
            )
