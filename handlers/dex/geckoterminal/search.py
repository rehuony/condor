"""Token search by symbol or address."""

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

from ._format import (
    NETWORK_NAMES,
    POPULAR_NETWORKS,
    _extract_pools_from_response,
    _format_pool_line,
    _get_pool_symbols,
)
from .state import get_gecko_state, set_gecko_network

logger = logging.getLogger(__name__)


async def handle_gecko_search(update: Update, context: Context) -> None:
    """Prompt user to enter token address for search"""
    context.user_data["dex_state"] = "gecko_search"

    # Get selected network
    network, _ = get_gecko_state(context.user_data)
    network_display = NETWORK_NAMES.get(network, network.title())

    keyboard = [
        [
            InlineKeyboardButton(
                text=f"🌐 {network_display}", callback_data="dex:gecko_search_network"
            )
        ],
        [InlineKeyboardButton(text="↩️ Cancel", callback_data="dex:gecko_explore")],
    ]

    message = (
        r"🔍 *Token Search*" + "\n\n"
        f"Network: *{escape_markdown_v2(network_display)}*\n\n"
        "Enter a token address to find pools:\n\n"
        "_Tap network button to change_"
    )

    await update.callback_query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_gecko_search_network(update: Update, context: Context) -> None:
    """Show network selection for token search"""
    keyboard = []

    # Add popular networks in rows of 3
    row = []
    for network in POPULAR_NETWORKS:
        display = NETWORK_NAMES.get(network, network.title())
        row.append(
            InlineKeyboardButton(
                text=display, callback_data=f"dex:gecko_search_set_net:{network}"
            )
        )
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append(
        [InlineKeyboardButton(text="« Back", callback_data="dex:gecko_search")]
    )

    current_network, _ = get_gecko_state(context.user_data)
    current_display = NETWORK_NAMES.get(current_network, current_network.title())

    message = (
        r"🌐 *Select Network for Search*" + "\n\n"
        f"Current: *{escape_markdown_v2(current_display)}*\n\n"
        "_Choose a network:_"
    )

    await update.callback_query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_gecko_search_set_network(
    update: Update, context: Context, network: str
) -> None:
    """Set network for search and return to search prompt"""
    set_gecko_network(context.user_data, network)
    display = NETWORK_NAMES.get(network, network.title())
    await update.callback_query.answer(f"Network: {display}")
    await handle_gecko_search(update, context)


async def process_gecko_search(
    update: Update, context: Context, user_input: str
) -> None:
    """Process token search input - uses selected network"""
    context.user_data.pop("dex_state", None)

    # Get the selected network
    selected_network, _ = get_gecko_state(context.user_data)
    network_display = NETWORK_NAMES.get(selected_network, selected_network.title())

    # Show loading message
    loading_msg = await update.message.answer(
        f"🔍 *Searching on {escape_markdown_v2(network_display)}\\.\\.\\.*",
        parse_mode="MarkdownV2",
    )

    try:
        token_address = user_input.strip()

        # Use the selected network directly
        result = await gecko_call(
            "get_top_pools_by_network_token", selected_network, token_address
        )
        pools = _extract_pools_from_response(result, 10)

        if not pools:
            await loading_msg.edit_text(
                r"❌ *No pools found*" + "\n\n"
                f"No pools found for this token on {escape_markdown_v2(network_display)}\\.\n\n"
                "_Try a different network or token address_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔍 Try Again", callback_data="dex:gecko_search"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="« Back", callback_data="dex:gecko_explore"
                            )
                        ],
                    ]
                ),
            )
            return

        # Store pools for selection
        context.user_data["gecko_pools"] = pools
        context.user_data["gecko_view"] = "search"
        context.user_data["gecko_network"] = selected_network
        context.user_data["gecko_search_token"] = token_address

        # Build message
        lines = [f"🔍 *Pools for Token \\- {escape_markdown_v2(network_display)}*\n"]
        # Show truncated or full address
        if len(token_address) > 24:
            lines.append(
                f"Token: `{escape_markdown_v2(token_address[:12])}...{escape_markdown_v2(token_address[-8:])}`\n"
            )
        else:
            lines.append(f"Token: `{escape_markdown_v2(token_address)}`\n")

        for i, pool in enumerate(pools, 1):
            line = _format_pool_line(pool, i)
            lines.append(escape_markdown_v2(line))

        lines.append("\n_Select a pool for details:_")

        # Build keyboard with pool buttons
        keyboard = []
        row = []
        for i, pool in enumerate(pools):
            base, quote = _get_pool_symbols(pool)
            btn = InlineKeyboardButton(
                text=f"{i+1}. {base[:6]}/{quote[:4]}",
                callback_data=f"dex:gecko_pool:{i}",
            )
            row.append(btn)
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        keyboard.append(
            [
                InlineKeyboardButton(
                    text="🔍 New Search", callback_data="dex:gecko_search"
                ),
                InlineKeyboardButton(text="« Back", callback_data="dex:gecko_explore"),
            ]
        )

        await loading_msg.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )

    except Exception as e:
        logger.error(f"Error searching token: {e}", exc_info=True)
        await loading_msg.edit_text(
            ui.notice("error", "Search failed", str(e)),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="« Back", callback_data="dex:gecko_explore"
                        )
                    ]
                ]
            ),
        )
