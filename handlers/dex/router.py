"""
DEX Callback and Message Router

Central routing for all DEX-related callback queries and text messages.
Dispatches to appropriate handlers based on action patterns.
"""

import logging
from functools import partial
from typing import Awaitable, Callable

from condor.telegram.errors import is_not_modified
from condor.telegram.types import (
    Context,
    Update,
)
from handlers._routing import PrefixRoute, dispatch_action, none_if_all, noop
from utils.auth import restricted
from utils.telegram_formatters import format_error_message

from .geckoterminal import (
    handle_back_to_list,
    handle_copy_address,
    handle_gecko_add_liquidity,
    handle_gecko_add_tokens,
    handle_gecko_networks,
    handle_gecko_new,
    handle_gecko_pool_tf,
    handle_gecko_refresh,
    handle_gecko_restart_gateway,
    handle_gecko_search,
    handle_gecko_search_network,
    handle_gecko_search_set_network,
    handle_gecko_select_network,
    handle_gecko_set_network,
    handle_gecko_show_pools,
    handle_gecko_swap,
    handle_gecko_toggle_view,
    handle_gecko_token_add,
    handle_gecko_token_info,
    handle_gecko_token_search,
    handle_gecko_top,
    handle_gecko_trending,
    process_gecko_search,
    show_gecko_charts_menu,
    show_gecko_combined,
    show_gecko_explore_menu,
    show_gecko_info,
    show_gecko_liquidity,
    show_network_menu,
    show_new_pools,
    show_ohlcv_chart,
    show_pool_detail,
    show_recent_trades,
    show_top_pools,
    show_trending_pools,
)
from .liquidity import (
    handle_liquidity,
    handle_lp_collect_all,
    handle_lp_hist_clear,
    handle_lp_hist_filter_connector,
    handle_lp_hist_filter_pair,
    handle_lp_hist_filter_status,
    handle_lp_hist_page,
    handle_lp_hist_set_filter,
    handle_lp_history,
    handle_lp_pos_view,
    handle_lp_refresh,
)
from .lp_monitor_handlers import (
    handle_lpm_cancel_countdown,
    handle_lpm_collect_fees,
    handle_lpm_detail,
    handle_lpm_dismiss,
    handle_lpm_navigation,
    handle_lpm_oor_navigation,
    handle_lpm_rebalance,
    handle_lpm_rebalance_execute,
    handle_lpm_skip,
)
from .menu import cancel_dex_loading_task, handle_close, handle_refresh
from .pools import (
    handle_add_position,
    handle_add_to_gateway,
    handle_manage_positions,
    handle_plot_liquidity,
    handle_pool_combined_chart,
    handle_pool_detail_refresh,
    handle_pool_info,
    handle_pool_list,
    handle_pool_list_back,
    handle_pool_list_orca,
    handle_pool_ohlcv,
    handle_pool_select,
    handle_pos_add_confirm,
    handle_pos_close_confirm,
    handle_pos_close_execute,
    handle_pos_collect_fees,
    handle_pos_help,
    handle_pos_refresh,
    handle_pos_set_base,
    handle_pos_set_connector,
    handle_pos_set_lower,
    handle_pos_set_network,
    handle_pos_set_pool,
    handle_pos_set_quote,
    handle_pos_set_upper,
    handle_pos_toggle_strategy,
    handle_pos_use_max_range,
    handle_pos_view,
    handle_pos_view_pool,
    handle_position_list,
    process_add_position,
    process_pool_info,
    process_pool_list,
    process_pos_set_base,
    process_pos_set_connector,
    process_pos_set_lower,
    process_pos_set_network,
    process_pos_set_pool,
    process_pos_set_quote,
    process_pos_set_upper,
    process_position_list,
    show_add_position_menu,
)

# Import all handler modules
from .swap import (
    handle_swap,
    handle_swap_connector_select,
    handle_swap_execute_confirm,
    handle_swap_get_quote,
    handle_swap_hist_clear,
    handle_swap_hist_filter_connector,
    handle_swap_hist_filter_pair,
    handle_swap_hist_filter_status,
    handle_swap_hist_page,
    handle_swap_hist_set_filter,
    handle_swap_history,
    handle_swap_network_select,
    handle_swap_refresh,
    handle_swap_set_amount,
    handle_swap_set_connector,
    handle_swap_set_network,
    handle_swap_set_pair,
    handle_swap_set_slippage,
    handle_swap_status,
    handle_swap_toggle_side,
    process_swap,
    process_swap_set_amount,
    process_swap_set_pair,
    process_swap_set_slippage,
    process_swap_status,
)

logger = logging.getLogger(__name__)

# Type alias for handler functions


# ============================================
# SLOW ACTIONS (require typing indicator)
# ============================================

SLOW_ACTIONS = frozenset(
    {
        "main_menu",
        "swap",
        "swap_refresh",
        "swap_get_quote",
        "swap_execute_confirm",
        "swap_history",
        "swap_hist_clear",
        "swap_hist_filter_pair",
        "swap_hist_filter_connector",
        "swap_hist_filter_status",
        "swap_hist_page_prev",
        "swap_hist_page_next",
        "liquidity",
        "lp_refresh",
        "lp_history",
        "lp_collect_all",
        "lp_hist_clear",
        "lp_hist_filter_pair",
        "lp_hist_filter_connector",
        "lp_hist_filter_status",
        "lp_hist_page_prev",
        "lp_hist_page_next",
        "pool_info",
        "pool_list",
        "manage_positions",
        "pos_add_confirm",
        "pos_close_exec",
        "add_to_gateway",
        "pool_detail_refresh",
        "gecko_networks",
        "gecko_trades",
        "gecko_show_pools",
        "gecko_refresh",
        "gecko_token_search",
        "gecko_token_add",
        "gecko_explore",
        "gecko_swap",
        "gecko_info",
        "gecko_add_tokens",
        "gecko_restart_gateway",
    }
)

SLOW_PREFIXES = (
    "gecko_trending_",
    "gecko_top_",
    "gecko_new_",
    "gecko_pool:",
    "gecko_ohlcv:",
    "gecko_pool_tf:",
    "gecko_token:",
    "swap_hist_set_",
    "lp_hist_set_",
    "lpm_nav:",
    "lpm_cancel_countdown:",
    "pos_close:",
)


def _is_slow_action(action: str) -> bool:
    """Check if an action requires a typing indicator."""
    return action in SLOW_ACTIONS or action.startswith(SLOW_PREFIXES)


async def _copy_pool_address(update: Update, context: Context, _rest: str) -> None:
    query = update.callback_query
    selected_pool = context.user_data.get("selected_pool", {})
    pool_address = selected_pool.get(
        "pool_address", selected_pool.get("address", "N/A")
    )
    await query.answer("Address sent below ⬇️")
    await query.message.answer(f"`{pool_address}`", parse_mode="Markdown")


async def _add_position_from_pool(update: Update, context: Context) -> None:
    query = update.callback_query
    await query.answer("Loading position form...")
    selected_pool = context.user_data.get("selected_pool", {})
    if selected_pool:
        pool_address = selected_pool.get(
            "pool_address", selected_pool.get("address", "")
        )
        context.user_data["add_position_params"] = {
            "connector": selected_pool.get("connector", "meteora"),
            "network": "solana-mainnet-beta",
            "pool_address": pool_address,
            "lower_price": "",
            "upper_price": "",
            "amount_base": "10%",
            "amount_quote": "10%",
            "strategy_type": "0",
        }
    await show_add_position_menu(update, context)


# ============================================
# CALLBACK ACTION DISPATCH TABLE
# ============================================

# Simple action -> handler mapping (no parameters)
SIMPLE_ACTIONS: dict[str, Callable[[Update, Context], Awaitable[None]]] = {
    # Menu
    "main_menu": handle_swap,
    "close": handle_close,
    "refresh": handle_refresh,
    "noop": noop,  # No-op for page indicators
    "lpm_noop": noop,
    "swap_hist_page_prev": partial(handle_swap_hist_page, direction="prev"),
    "swap_hist_page_next": partial(handle_swap_hist_page, direction="next"),
    "lp_hist_page_prev": partial(handle_lp_hist_page, direction="prev"),
    "lp_hist_page_next": partial(handle_lp_hist_page, direction="next"),
    "add_position_from_pool": _add_position_from_pool,
    # Swap
    "swap": handle_swap,
    "swap_refresh": handle_swap_refresh,
    "swap_toggle_side": handle_swap_toggle_side,
    "swap_set_connector": handle_swap_set_connector,
    "swap_set_network": handle_swap_set_network,
    "swap_set_pair": handle_swap_set_pair,
    "swap_set_amount": handle_swap_set_amount,
    "swap_set_slippage": handle_swap_set_slippage,
    "swap_get_quote": handle_swap_get_quote,
    "swap_execute_confirm": handle_swap_execute_confirm,
    "swap_history": handle_swap_history,
    "swap_status": handle_swap_status,
    "swap_hist_filter_pair": handle_swap_hist_filter_pair,
    "swap_hist_filter_connector": handle_swap_hist_filter_connector,
    "swap_hist_filter_status": handle_swap_hist_filter_status,
    "swap_hist_clear": handle_swap_hist_clear,
    # Legacy swap redirects
    "swap_quote": handle_swap,
    "swap_execute": handle_swap,
    "swap_search": handle_swap_history,
    # Liquidity
    "liquidity": handle_liquidity,
    "lp_refresh": handle_lp_refresh,
    "lp_collect_all": handle_lp_collect_all,
    "lp_history": handle_lp_history,
    "lp_hist_filter_pair": handle_lp_hist_filter_pair,
    "lp_hist_filter_connector": handle_lp_hist_filter_connector,
    "lp_hist_filter_status": handle_lp_hist_filter_status,
    "lp_hist_clear": handle_lp_hist_clear,
    # Legacy liquidity redirects
    "explore_pools": handle_lp_refresh,
    # Pool
    "pool_info": handle_pool_info,
    "pool_list": handle_pool_list,
    "pool_list_orca": handle_pool_list_orca,
    "pool_list_back": handle_pool_list_back,
    "pool_detail_refresh": handle_pool_detail_refresh,
    "add_to_gateway": handle_add_to_gateway,
    # Position management
    "manage_positions": handle_manage_positions,
    "position_list": handle_position_list,
    "add_position": handle_add_position,
    "pos_set_connector": handle_pos_set_connector,
    "pos_set_network": handle_pos_set_network,
    "pos_set_pool": handle_pos_set_pool,
    "pos_set_lower": handle_pos_set_lower,
    "pos_set_upper": handle_pos_set_upper,
    "pos_set_base": handle_pos_set_base,
    "pos_set_quote": handle_pos_set_quote,
    "pos_add_confirm": handle_pos_add_confirm,
    "pos_use_max_range": handle_pos_use_max_range,
    "pos_help": handle_pos_help,
    "pos_toggle_strategy": handle_pos_toggle_strategy,
    "pos_refresh": handle_pos_refresh,
    # GeckoTerminal
    "gecko_explore": show_gecko_explore_menu,
    "gecko_toggle_view": handle_gecko_toggle_view,
    "gecko_select_network": handle_gecko_select_network,
    "gecko_show_pools": handle_gecko_show_pools,
    "gecko_refresh": handle_gecko_refresh,
    "gecko_trending": handle_gecko_trending,
    "gecko_top": handle_gecko_top,
    "gecko_new": handle_gecko_new,
    "gecko_networks": handle_gecko_networks,
    "gecko_search": handle_gecko_search,
    "gecko_search_network": handle_gecko_search_network,
    "gecko_charts": show_gecko_charts_menu,
    "gecko_add_liquidity": handle_gecko_add_liquidity,
    "gecko_token_search": handle_gecko_token_search,
    "gecko_token_add": handle_gecko_token_add,
    "gecko_swap": handle_gecko_swap,
    "gecko_info": show_gecko_info,
    "gecko_liquidity": show_gecko_liquidity,
    "gecko_trades": show_recent_trades,
    "gecko_copy_addr": handle_copy_address,
    "gecko_back_to_list": handle_back_to_list,
    "gecko_add_tokens": handle_gecko_add_tokens,
    "gecko_restart_gateway": handle_gecko_restart_gateway,
}


# ============================================
# CEX SWITCH HANDLER
# ============================================


async def _handle_switch_to_cex(
    update: Update, context: Context, connector_name: str
) -> None:
    """Switch from DEX to CEX trading"""
    from condor.preferences import (
        get_clob_order_defaults,
        set_last_trade_connector,
    )
    from handlers.cex.trade import handle_trade as cex_handle_trade

    # Clear DEX state
    context.user_data.pop("dex_state", None)
    context.user_data.pop("swap_params", None)

    # Save preference and set up CEX trade
    set_last_trade_connector(context.user_data, "cex", connector_name)
    defaults = get_clob_order_defaults(context.user_data)
    defaults["connector"] = connector_name
    context.user_data["trade_params"] = defaults

    # Route to CEX trade menu
    await cex_handle_trade(update, context)


# ============================================
# PARAMETERIZED ACTION HANDLERS
# ============================================


def _filter(
    handler: Callable[..., Awaitable[None]], filter_type: str
) -> Callable[..., Awaitable[None]]:
    """Bind the history filter name; the button carries only the value."""

    async def run(update: Update, context: Context, value: str) -> None:
        await handler(update, context, filter_type, value)

    return run


def _with_default(default: str) -> Callable[[str], tuple[str, str]]:
    """Parse ``{first}[:{second}]`` into a pair, filling in ``default``."""

    def parse(rest: str) -> tuple[str, str]:
        parts = rest.split(":")
        return parts[0], parts[1] if len(parts) > 1 else default

    return parse


def _instance_and_int(rest: str) -> tuple[str, int] | None:
    """Parse ``{instance_id}:{index}``; None when the index is missing."""
    parts = rest.split(":")
    if len(parts) < 2:
        return None
    return parts[0], int(parts[1])


def _instance_and_str(rest: str) -> tuple[str, str] | None:
    parts = rest.split(":")
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def _pair(
    handler: Callable[..., Awaitable[None]], **kwargs
) -> Callable[..., Awaitable[None]]:
    """Adapt a two-parameter handler to the ``(update, context, parsed)`` shape.

    A ``None`` parse result means the callback carried too few parts; it is
    consumed silently, as the missing-parameter branches always were.
    """

    async def run(update: Update, context: Context, parsed) -> None:
        if parsed is not None:
            await handler(update, context, *parsed, **kwargs)

    return run


async def _pos_view_tf(
    update: Update, context: Context, parsed: tuple[str, str]
) -> None:
    pos_index, timeframe = parsed
    await handle_pos_view(update, context, pos_index, timeframe=timeframe)


#: Prefixed actions in match order; a longer prefix precedes a shorter one it
#: starts with (``lpm_rebalance_confirm:`` before ``lpm_rebalance:``).
PREFIX_ACTIONS: tuple[PrefixRoute, ...] = (
    ("swap_connector_", handle_swap_connector_select, str),
    ("swap_network_", handle_swap_network_select, str),
    ("switch_cex_", _handle_switch_to_cex, str),
    ("swap_hist_set_pair_", _filter(handle_swap_hist_set_filter, "pair"), str),
    (
        "swap_hist_set_connector_",
        _filter(handle_swap_hist_set_filter, "connector"),
        str,
    ),
    ("swap_hist_set_status_", _filter(handle_swap_hist_set_filter, "status"), str),
    ("lp_hist_set_pair_", _filter(handle_lp_hist_set_filter, "pair"), str),
    ("lp_hist_set_connector_", _filter(handle_lp_hist_set_filter, "connector"), str),
    ("lp_hist_set_status_", _filter(handle_lp_hist_set_filter, "status"), str),
    ("lp_pos_view:", handle_lp_pos_view, int),
    ("pool_select:", handle_pool_select, int),
    ("pool_tf:", lambda u, c, tf: handle_pool_detail_refresh(u, c, timeframe=tf), str),
    ("plot_liquidity:", handle_plot_liquidity, int),
    ("pos_view_tf:", _pos_view_tf, _with_default("1h")),
    ("pos_view_pool:", handle_pos_view_pool, str),
    ("pos_view:", handle_pos_view, str),
    ("pos_collect:", handle_pos_collect_fees, str),
    ("pos_close_exec:", handle_pos_close_execute, str),
    ("pos_close:", handle_pos_close_confirm, str),
    ("pos_tf:", lambda u, c, tf: handle_pos_refresh(u, c, timeframe=tf), str),
    ("copy_pool:", _copy_pool_address, str),
    ("gecko_set_network:", handle_gecko_set_network, str),
    ("gecko_trending_", show_trending_pools, none_if_all),
    ("gecko_top_", show_top_pools, str),
    ("gecko_new_", show_new_pools, none_if_all),
    ("gecko_net_", show_network_menu, str),
    ("gecko_search_set_net:", handle_gecko_search_set_network, str),
    ("gecko_pool_tf:", handle_gecko_pool_tf, str),
    ("gecko_pool:", show_pool_detail, int),
    ("gecko_token:", handle_gecko_token_info, str),
    ("gecko_ohlcv:", show_ohlcv_chart, str),
    ("gecko_combined:", show_gecko_combined, str),
    ("pool_ohlcv:", _pair(handle_pool_ohlcv), _with_default("usd")),
    ("pool_combined:", _pair(handle_pool_combined_chart), _with_default("usd")),
    ("lpm_skip:", handle_lpm_skip, str),
    ("lpm_nav:", _pair(handle_lpm_navigation), _instance_and_int),
    ("lpm_dismiss:", lambda u, c, _rest: handle_lpm_dismiss(u, c), str),
    ("lpm_detail:", _pair(handle_lpm_detail), _instance_and_int),
    ("lpm_collect:", handle_lpm_collect_fees, str),
    ("lpm_rebalance_confirm:", handle_lpm_rebalance_execute, str),
    ("lpm_rebalance:", handle_lpm_rebalance, str),
    ("lpm_oor:", _pair(handle_lpm_oor_navigation), _instance_and_int),
    ("lpm_cancel_countdown:", _pair(handle_lpm_cancel_countdown), _instance_and_str),
)


# ============================================
# MESSAGE STATE HANDLERS
# ============================================

# State -> processor function mapping
MESSAGE_STATE_HANDLERS: dict[str, Callable] = {
    # Swap states
    "swap": process_swap,
    "swap_set_pair": process_swap_set_pair,
    "swap_set_amount": process_swap_set_amount,
    "swap_set_slippage": process_swap_set_slippage,
    "swap_status": process_swap_status,
    # Pool states
    "pool_info": process_pool_info,
    "pool_list": process_pool_list,
    "position_list": process_position_list,
    # Add position states
    "add_position": process_add_position,
    "pos_set_connector": process_pos_set_connector,
    "pos_set_network": process_pos_set_network,
    "pos_set_pool": process_pos_set_pool,
    "pos_set_lower": process_pos_set_lower,
    "pos_set_upper": process_pos_set_upper,
    "pos_set_base": process_pos_set_base,
    "pos_set_quote": process_pos_set_quote,
    # GeckoTerminal
    "gecko_search": process_gecko_search,
}

# States that should be cleared after processing
CLEAR_STATE_AFTER = frozenset(
    {"swap", "swap_status", "pool_info", "pool_list", "position_list", "add_position"}
)


# ============================================
# MAIN CALLBACK HANDLER
# ============================================


@restricted
async def dex_callback_handler(update: Update, context: Context) -> None:
    """Handle inline button callbacks - Routes to appropriate sub-module."""
    query = update.callback_query
    await query.answer()

    try:
        # Parse action from callback data (format: dex:{action})
        callback_parts = query.data.split(":", 1)
        action = callback_parts[1] if len(callback_parts) > 1 else query.data

        # Cancel any pending menu loading task when navigating away
        if action != "main_menu":
            cancel_dex_loading_task(context)

        # Show typing indicator for slow operations
        if _is_slow_action(action):
            await query.message.bot.send_chat_action(
                chat_id=query.message.chat.id, action="typing"
            )

        if await dispatch_action(
            update, context, action, SIMPLE_ACTIONS, PREFIX_ACTIONS
        ):
            return

        # Unknown action
        await query.message.answer(f"Unknown action: {action}")

    except Exception as e:
        # Ignore "message is not modified" errors - they're harmless
        if is_not_modified(e):
            logger.debug(f"Message not modified (ignored): {e}")
            return

        logger.error(f"Error in DEX callback handler: {e}", exc_info=True)
        error_message = format_error_message(f"Operation failed: {str(e)}")
        try:
            await query.message.answer(error_message, parse_mode="MarkdownV2")
        except Exception as reply_error:
            logger.warning(f"Failed to send error message: {reply_error}")


# ============================================
# MAIN MESSAGE HANDLER
# ============================================


@restricted
async def dex_message_handler(update: Update, context: Context) -> None:
    """Handle user text input - Routes to appropriate processor."""
    dex_state = context.user_data.get("dex_state")

    if not dex_state:
        return

    user_input = update.message.text.strip()
    logger.info(f"DEX message handler - state: {dex_state}, input: {user_input}")

    try:
        # Clear state for operations that complete
        if dex_state in CLEAR_STATE_AFTER:
            context.user_data.pop("dex_state", None)

        # Dispatch to appropriate handler
        handler = MESSAGE_STATE_HANDLERS.get(dex_state)
        if handler:
            await handler(update, context, user_input)
        else:
            await update.message.answer(f"Unknown state: {dex_state}")

    except Exception as e:
        logger.error(f"Error processing DEX input: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to process input: {str(e)}")
        await update.message.answer(error_message, parse_mode="MarkdownV2")
