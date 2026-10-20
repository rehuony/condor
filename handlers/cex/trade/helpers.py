"""Formatting and cache-key helpers for the trade menu."""

import logging

from handlers.dex._shared import format_relative_time
from utils.telegram_formatters import (
    escape_markdown_v2,
    format_compact_number,
)

logger = logging.getLogger(__name__)


# Default leverage when not set
DEFAULT_LEVERAGE = 5


def _format_price(price: float) -> str:
    """Format price with appropriate precision"""
    if price >= 10000:
        return f"${price:,.0f}"
    elif price >= 1000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:.4f}".rstrip("0").rstrip(".")
    elif price >= 0.0001:
        return f"${price:.6f}".rstrip("0").rstrip(".")
    else:
        return f"${price:.8f}".rstrip("0").rstrip(".")


def _is_perpetual_connector(connector_name: str) -> bool:
    """Check if connector is a perpetual futures exchange"""
    return "perpetual" in connector_name.lower() if connector_name else False


def _get_leverage_cache_key(connector: str, trading_pair: str) -> str:
    """Get cache key for leverage"""
    return f"leverage_{connector}_{trading_pair}"


def _get_position_mode_cache_key(connector: str) -> str:
    """Get cache key for position mode"""
    return f"pos_mode_{connector}"


async def _fetch_recent_orders(client, limit: int = 5) -> list:
    """Fetch recent orders for display"""
    try:
        result = await client.trading.get_active_orders(limit=limit)
        return result.get("data", [])
    except Exception as e:
        logger.warning(f"Error fetching recent orders: {e}")
        return []


def _format_compact_order_line(order: dict) -> str:
    """Format a single order as a compact line (like swap format)

    Returns: "✅ BTC-USDT BUY @95000 0.01  1d bin"
    """
    pair = order.get("trading_pair", "N/A")
    side = order.get("trade_type", order.get("side", "?"))
    status = order.get("status", "UNKNOWN")
    amount = order.get("amount", 0)
    price = order.get("price", 0)
    connector = order.get("connector_name", "")[:3]
    timestamp = order.get("creation_timestamp") or order.get("timestamp", "")

    # Status emoji
    status_char = (
        "✅"
        if status == "FILLED"
        else "🟢" if status == "OPEN" else "⏳" if status == "PENDING" else "❌"
    )

    # Format price
    price_str = f"@{format_compact_number(price, 4)}" if price else "@MKT"

    # Build line like swap format: "✅ SOL-USDT BUY @144.51 0.5  1d bin"
    line = f"{status_char} {pair} {side} {price_str} {format_compact_number(amount)}"

    # Add metadata (age and connector)
    meta_parts = []
    age = format_relative_time(str(timestamp)) if timestamp else ""
    if age:
        meta_parts.append(age)
    if connector:
        meta_parts.append(connector)

    if meta_parts:
        line += f"  {' '.join(meta_parts)}"

    return escape_markdown_v2(line)


def _format_compact_position_line(pos: dict) -> str:
    """Format a single position as a compact line

    Returns: "🟢 binance_perpetual SOL-USDT LONG @144.50 0.5 PnL:+$1.23"
    """
    connector = pos.get("connector_name", "")
    pair = pos.get("trading_pair", "N/A")
    side = pos.get("position_side") or pos.get("side") or pos.get("trade_type", "?")
    amount = pos.get("amount", 0)
    entry_price = pos.get("entry_price", 0)
    pnl = pos.get("unrealized_pnl", 0)

    # Side display
    side_upper = str(side).upper()
    side_emoji = "🟢" if side_upper in ("LONG", "BUY") else "🔴"
    side_display = "LONG" if side_upper in ("LONG", "BUY") else "SHORT"

    # PnL with sign
    try:
        pnl_float = float(pnl)
        pnl_str = f"+${pnl_float:.2f}" if pnl_float >= 0 else f"-${abs(pnl_float):.2f}"
    except (ValueError, TypeError):
        pnl_str = "—"

    line = f"{side_emoji} {connector} {pair} {side_display} @{format_compact_number(entry_price, 4)} {format_compact_number(amount)} PnL:{pnl_str}"

    return escape_markdown_v2(line)
