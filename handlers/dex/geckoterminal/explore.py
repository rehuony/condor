"""Explore menu: trending, top and new pool listings."""

import logging

from condor.pool_data import (
    gecko_call,
)
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from utils import telegram_ui as ui
from utils.telegram_formatters import escape_markdown_v2

from .._shared import cached_call
from ._format import (
    NETWORK_NAMES,
    POPULAR_NETWORKS,
    TRENDING_CACHE_TTL,
    _extract_pools_from_response,
    _format_gecko_pool_table,
    _get_pool_symbols,
)
from .state import (
    VIEW_LABELS,
    cycle_gecko_view,
    get_gecko_state,
    set_gecko_network,
    set_gecko_view,
)

logger = logging.getLogger(__name__)


async def show_gecko_explore_menu(update: Update, context: Context) -> None:
    """Display GeckoTerminal pools directly with network/view controls"""
    # Directly show pools based on current settings
    await handle_gecko_show_pools(update, context)


async def handle_gecko_toggle_view(update: Update, context: Context) -> None:
    """Toggle between trending/top/new views"""
    new_view = cycle_gecko_view(context.user_data)
    await update.callback_query.answer(
        f"Switched to {VIEW_LABELS.get(new_view, new_view)}"
    )
    await show_gecko_explore_menu(update, context)


async def handle_gecko_select_network(update: Update, context: Context) -> None:
    """Show network selection menu"""
    keyboard = []

    # Add popular networks in rows of 3
    row = []
    for network in POPULAR_NETWORKS:
        display = NETWORK_NAMES.get(network, network.title())
        row.append(
            InlineKeyboardButton(
                text=display, callback_data=f"dex:gecko_set_network:{network}"
            )
        )
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # Add more networks option and back
    keyboard.append(
        [
            InlineKeyboardButton(
                text="🌍 More Networks", callback_data="dex:gecko_networks"
            ),
        ]
    )
    keyboard.append(
        [InlineKeyboardButton(text="« Back", callback_data="dex:gecko_explore")]
    )

    current_network, _ = get_gecko_state(context.user_data)
    current_display = NETWORK_NAMES.get(current_network, current_network.title())

    message = (
        r"🌐 *Select Network*" + "\n\n"
        f"Current: *{escape_markdown_v2(current_display)}*\n\n"
        "_Choose a network:_"
    )

    await update.callback_query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_gecko_set_network(
    update: Update, context: Context, network: str
) -> None:
    """Set the selected network and return to explore menu"""
    set_gecko_network(context.user_data, network)
    display = NETWORK_NAMES.get(network, network.title())
    await update.callback_query.answer(f"Network: {display}")
    await show_gecko_explore_menu(update, context)


async def handle_gecko_show_pools(update: Update, context: Context) -> None:
    """Show pools based on current network and view selection"""
    network, view = get_gecko_state(context.user_data)

    if view == "trending":
        await show_trending_pools(update, context, network)
    elif view == "top":
        await show_top_pools(update, context, network)
    elif view == "new":
        await show_new_pools(update, context, network)


def _build_pool_list_keyboard(pools: list, user_data: dict) -> InlineKeyboardMarkup:
    """Build keyboard with pool buttons and network/view controls"""
    network, view = get_gecko_state(user_data)
    network_display = NETWORK_NAMES.get(network, network.title())

    keyboard = []

    # Pool selection buttons (2 per row)
    row = []
    for i, pool in enumerate(pools):
        base, quote = _get_pool_symbols(pool)
        btn = InlineKeyboardButton(
            text=f"{i+1}. {base[:6]}/{quote[:4]}", callback_data=f"dex:gecko_pool:{i}"
        )
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # Network and View controls
    keyboard.append(
        [
            InlineKeyboardButton(
                text=f"🌐 {network_display}", callback_data="dex:gecko_select_network"
            ),
            InlineKeyboardButton(
                text=VIEW_LABELS.get(view, "🔥 Trending"),
                callback_data="dex:gecko_toggle_view",
            ),
        ]
    )

    # Search and Refresh
    keyboard.append(
        [
            InlineKeyboardButton(text="🔍 Search", callback_data="dex:gecko_search"),
            InlineKeyboardButton(text="🔄 Refresh", callback_data="dex:gecko_refresh"),
        ]
    )

    # Back button
    keyboard.append(
        [
            InlineKeyboardButton(
                text="« Back to LP Menu", callback_data="dex:liquidity"
            ),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def handle_gecko_refresh(update: Update, context: Context) -> None:
    """Refresh current pool list by invalidating cache"""
    network, view = get_gecko_state(context.user_data)

    # Invalidate cache for current view
    cache_key = f"gecko_{view}_{network or 'all'}"
    if cache_key in context.user_data:
        del context.user_data[cache_key]

    await update.callback_query.answer("Refreshing...")
    await handle_gecko_show_pools(update, context)


async def handle_gecko_trending(update: Update, context: Context) -> None:
    """Show trending pools menu"""
    keyboard = [
        [
            InlineKeyboardButton(
                text="🌍 All Networks", callback_data="dex:gecko_trending_all"
            ),
        ],
    ]

    # Add popular network buttons
    row = []
    for network in POPULAR_NETWORKS:
        display = NETWORK_NAMES.get(network, network.title())
        row.append(
            InlineKeyboardButton(
                text=display, callback_data=f"dex:gecko_trending_{network}"
            )
        )
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append(
        [InlineKeyboardButton(text="« Back", callback_data="dex:gecko_explore")]
    )

    message = r"🔥 *Trending Pools*" + "\n\n" "Select network to view trending pools:"

    await update.callback_query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def show_trending_pools(
    update: Update, context: Context, network: str = None
) -> None:
    """Fetch and display trending pools"""
    query = update.callback_query

    network_name = (
        NETWORK_NAMES.get(network, network.title()) if network else "All Networks"
    )
    chat = query.message.chat

    # Show loading - handle photo messages
    if getattr(query.message, "photo", None):
        try:
            await query.message.delete()
        except Exception:
            pass
        loading_msg = await chat.send_message(
            f"🔥 *Trending \\- {escape_markdown_v2(network_name)}*\n\n_Loading\\.\\.\\._",
            parse_mode="MarkdownV2",
        )
    else:
        await query.message.edit_text(
            f"🔥 *Trending \\- {escape_markdown_v2(network_name)}*\n\n_Loading\\.\\.\\._",
            parse_mode="MarkdownV2",
        )
        loading_msg = query.message

    try:
        cache_key = f"gecko_trending_{network or 'all'}"

        async def fetch_trending():
            if network and network != "all":
                result = await gecko_call("get_trending_pools_by_network", network)
            else:
                result = await gecko_call("get_trending_pools")
            logger.info(f"GeckoTerminal trending response type: {type(result)}")
            return _extract_pools_from_response(result, 10)

        pools = await cached_call(
            context.user_data, cache_key, fetch_trending, TRENDING_CACHE_TTL
        )

        # Store pools for selection and update state
        context.user_data["gecko_pools"] = pools
        context.user_data["gecko_view"] = "trending"
        set_gecko_view(context.user_data, "trending")
        if network:
            set_gecko_network(context.user_data, network)

        # Build message with table format
        header = (
            f"🔥 *Trending \\- {escape_markdown_v2(network_name)}* \\({len(pools)}\\)\n"
        )
        table = _format_gecko_pool_table(pools)
        footer = "\n_Select pool number:_"

        # Build keyboard with network/view controls
        reply_markup = _build_pool_list_keyboard(pools, context.user_data)

        await loading_msg.edit_text(
            header + table + footer, parse_mode="MarkdownV2", reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error fetching trending pools: {e}", exc_info=True)
        try:
            await loading_msg.edit_text(
                ui.notice("error", "Could not fetch trending pools", str(e)),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔁 Retry", callback_data="dex:gecko_refresh"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="« Back to LP Menu", callback_data="dex:liquidity"
                            )
                        ],
                    ]
                ),
            )
        except Exception:
            await chat.send_message(
                ui.notice("error", "Could not fetch trending pools", str(e)),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔁 Retry", callback_data="dex:gecko_refresh"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="« Back to LP Menu", callback_data="dex:liquidity"
                            )
                        ],
                    ]
                ),
            )


async def handle_gecko_top(update: Update, context: Context) -> None:
    """Show top pools menu"""
    keyboard = []

    # Add popular network buttons
    row = []
    for network in POPULAR_NETWORKS:
        display = NETWORK_NAMES.get(network, network.title())
        row.append(
            InlineKeyboardButton(text=display, callback_data=f"dex:gecko_top_{network}")
        )
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append(
        [InlineKeyboardButton(text="« Back", callback_data="dex:gecko_explore")]
    )

    message = r"📈 *Top Pools by Volume*" + "\n\n" "Select network to view top pools:"

    await update.callback_query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def show_top_pools(update: Update, context: Context, network: str) -> None:
    """Fetch and display top pools by volume"""
    query = update.callback_query

    network_name = NETWORK_NAMES.get(network, network.title())
    chat = query.message.chat

    # Show loading - handle photo messages
    if getattr(query.message, "photo", None):
        try:
            await query.message.delete()
        except Exception:
            pass
        loading_msg = await chat.send_message(
            f"📈 *Top Pools \\- {escape_markdown_v2(network_name)}*\n\n_Loading\\.\\.\\._",
            parse_mode="MarkdownV2",
        )
    else:
        await query.message.edit_text(
            f"📈 *Top Pools \\- {escape_markdown_v2(network_name)}*\n\n_Loading\\.\\.\\._",
            parse_mode="MarkdownV2",
        )
        loading_msg = query.message

    try:
        cache_key = f"gecko_top_{network}"

        async def fetch_top():
            result = await gecko_call("get_top_pools_by_network", network)
            return _extract_pools_from_response(result, 10)

        pools = await cached_call(
            context.user_data, cache_key, fetch_top, TRENDING_CACHE_TTL
        )

        # Store pools for selection and update state
        context.user_data["gecko_pools"] = pools
        context.user_data["gecko_view"] = "top"
        set_gecko_view(context.user_data, "top")
        set_gecko_network(context.user_data, network)

        # Build message with table format
        header = f"📈 *Top Pools \\- {escape_markdown_v2(network_name)}* \\({len(pools)}\\)\n"
        table = _format_gecko_pool_table(pools)
        footer = "\n_Select pool number:_"

        # Build keyboard with network/view controls
        reply_markup = _build_pool_list_keyboard(pools, context.user_data)

        await loading_msg.edit_text(
            header + table + footer, parse_mode="MarkdownV2", reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error fetching top pools: {e}", exc_info=True)
        try:
            await loading_msg.edit_text(
                ui.notice("error", "Could not fetch top pools", str(e)),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔁 Retry", callback_data="dex:gecko_refresh"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="« Back to LP Menu", callback_data="dex:liquidity"
                            )
                        ],
                    ]
                ),
            )
        except Exception:
            await chat.send_message(
                ui.notice("error", "Could not fetch top pools", str(e)),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔁 Retry", callback_data="dex:gecko_refresh"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="« Back to LP Menu", callback_data="dex:liquidity"
                            )
                        ],
                    ]
                ),
            )


async def handle_gecko_new(update: Update, context: Context) -> None:
    """Show new pools menu"""
    keyboard = [
        [
            InlineKeyboardButton(
                text="🌍 All Networks", callback_data="dex:gecko_new_all"
            ),
        ],
    ]

    # Add popular network buttons
    row = []
    for network in POPULAR_NETWORKS:
        display = NETWORK_NAMES.get(network, network.title())
        row.append(
            InlineKeyboardButton(text=display, callback_data=f"dex:gecko_new_{network}")
        )
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append(
        [InlineKeyboardButton(text="« Back", callback_data="dex:gecko_explore")]
    )

    message = (
        r"🆕 *New Pools*" + "\n\n" "Select network to view recently created pools:"
    )

    await update.callback_query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def show_new_pools(update: Update, context: Context, network: str = None) -> None:
    """Fetch and display new pools"""
    query = update.callback_query
    chat = query.message.chat

    network_name = (
        NETWORK_NAMES.get(network, network.title()) if network else "All Networks"
    )

    # Show loading - handle photo messages
    if query.message.photo:
        try:
            await query.message.delete()
        except Exception:
            pass
        loading_msg = await chat.send_message(
            f"🆕 *New Pools \\- {escape_markdown_v2(network_name)}*\n\n_Loading\\.\\.\\._",
            parse_mode="MarkdownV2",
        )
    else:
        await query.message.edit_text(
            f"🆕 *New Pools \\- {escape_markdown_v2(network_name)}*\n\n_Loading\\.\\.\\._",
            parse_mode="MarkdownV2",
        )
        loading_msg = query.message

    try:
        cache_key = f"gecko_new_{network or 'all'}"

        async def fetch_new():
            if network and network != "all":
                result = await gecko_call("get_new_pools_by_network", network)
            else:
                result = await gecko_call("get_new_pools_all_networks")
            return _extract_pools_from_response(result, 10)

        pools = await cached_call(
            context.user_data, cache_key, fetch_new, TRENDING_CACHE_TTL
        )

        # Store pools for selection and update state
        context.user_data["gecko_pools"] = pools
        context.user_data["gecko_view"] = "new"
        set_gecko_view(context.user_data, "new")
        if network:
            set_gecko_network(context.user_data, network)

        # Build message with table format
        header = f"🆕 *New Pools \\- {escape_markdown_v2(network_name)}* \\({len(pools)}\\)\n"
        table = _format_gecko_pool_table(pools)
        footer = "\n_Select pool number:_"

        # Build keyboard with network/view controls
        reply_markup = _build_pool_list_keyboard(pools, context.user_data)

        await loading_msg.edit_text(
            header + table + footer, parse_mode="MarkdownV2", reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error fetching new pools: {e}", exc_info=True)
        try:
            await loading_msg.edit_text(
                ui.notice("error", "Could not fetch new pools", str(e)),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔁 Retry", callback_data="dex:gecko_refresh"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="« Back to LP Menu", callback_data="dex:liquidity"
                            )
                        ],
                    ]
                ),
            )
        except Exception:
            await chat.send_message(
                ui.notice("error", "Could not fetch new pools", str(e)),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔁 Retry", callback_data="dex:gecko_refresh"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="« Back to LP Menu", callback_data="dex:liquidity"
                            )
                        ],
                    ]
                ),
            )


async def handle_back_to_list(update: Update, context: Context) -> None:
    """Return to the previous pool list"""
    view = context.user_data.get("gecko_view", "trending")
    network = context.user_data.get("gecko_network")

    if view == "trending":
        await show_trending_pools(update, context, network)
    elif view == "top":
        await show_top_pools(update, context, network)
    elif view == "new":
        await show_new_pools(update, context, network)
    elif view == "search":
        # Re-run search
        token = context.user_data.get("gecko_search_token")
        if token:
            await show_gecko_explore_menu(update, context)
        else:
            await show_gecko_explore_menu(update, context)
    else:
        await show_gecko_explore_menu(update, context)
