"""Unified CEX trade menu: parameters, quote, execution and text input.

Each feature lives in its own module; this package re-exports the handlers the
CEX router wires up and keeps the close handler.
"""

import logging

from condor.telegram.types import Context, Update

from .execute import (
    handle_trade_execute,
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
from .input import (
    _show_pair_suggestions,
    _update_trade_menu_after_input,
    handle_trade_pair_select,
    process_trade,
    process_trade_set_amount,
    process_trade_set_leverage,
    process_trade_set_pair,
    process_trade_set_price,
)
from .menu import (
    _build_trade_keyboard,
    _build_trade_menu_text,
    _fetch_trade_data_background,
    _update_trade_message,
    handle_trade,
    handle_trade_refresh,
    show_trade_menu,
)
from .params import (
    _invalidate_trade_cache,
    handle_trade_connector_select,
    handle_trade_quick,
    handle_trade_set_amount,
    handle_trade_set_connector,
    handle_trade_set_leverage,
    handle_trade_set_pair,
    handle_trade_set_price,
    handle_trade_toggle_pos_mode,
    handle_trade_toggle_position,
    handle_trade_toggle_side,
    handle_trade_toggle_type,
)
from .quote import (
    handle_trade_get_quote,
)

logger = logging.getLogger(__name__)


async def handle_close(update: Update, context: Context) -> None:
    """Close the trade interface"""
    context.user_data.pop("cex_state", None)
    context.user_data.pop("trade_params", None)
    context.user_data.pop("trade_menu_message_id", None)
    context.user_data.pop("trade_menu_chat_id", None)

    try:
        await update.callback_query.message.delete()
    except Exception:
        pass


__all__ = [
    "DEFAULT_LEVERAGE",
    "_build_trade_keyboard",
    "_build_trade_menu_text",
    "_fetch_recent_orders",
    "_fetch_trade_data_background",
    "_format_compact_order_line",
    "_format_compact_position_line",
    "_format_price",
    "_get_leverage_cache_key",
    "_get_position_mode_cache_key",
    "_invalidate_trade_cache",
    "_is_perpetual_connector",
    "_show_pair_suggestions",
    "_update_trade_menu_after_input",
    "_update_trade_message",
    "handle_close",
    "handle_trade",
    "handle_trade_connector_select",
    "handle_trade_quick",
    "handle_trade_execute",
    "handle_trade_get_quote",
    "handle_trade_pair_select",
    "handle_trade_refresh",
    "handle_trade_set_amount",
    "handle_trade_set_connector",
    "handle_trade_set_leverage",
    "handle_trade_set_pair",
    "handle_trade_set_price",
    "handle_trade_toggle_pos_mode",
    "handle_trade_toggle_position",
    "handle_trade_toggle_side",
    "handle_trade_toggle_type",
    "process_trade",
    "process_trade_set_amount",
    "process_trade_set_leverage",
    "process_trade_set_pair",
    "process_trade_set_price",
    "show_trade_menu",
]
