"""Network selection menu."""

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
from ._format import NETWORK_NAMES, _extract_pools_from_response

logger = logging.getLogger(__name__)


async def handle_gecko_networks(update: Update, context: Context) -> None:
    """Show all available networks"""
    query = update.callback_query

    # Show loading
    await query.message.edit_text(
        r"🌐 *Networks*" + "\n\n" + r"_Loading\.\.\._", parse_mode="MarkdownV2"
    )

    try:
        cache_key = "gecko_networks"

        async def fetch_networks():
            result = await gecko_call("get_networks")
            return _extract_pools_from_response(result, 100)

        networks = await cached_call(
            context.user_data, cache_key, fetch_networks, 600  # Cache for 10 minutes
        )

        # Store networks and build keyboard (show first 20 most popular)
        keyboard = []
        row = []
        for network in networks[:20]:
            attrs = network.get("attributes", network)
            network_id = network.get("id", "")
            name = attrs.get("name", network_id)[:12]
            row.append(
                InlineKeyboardButton(
                    text=name, callback_data=f"dex:gecko_net_{network_id}"
                )
            )
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        keyboard.append(
            [InlineKeyboardButton(text="« Back", callback_data="dex:gecko_explore")]
        )

        message = r"🌐 *Select Network*" + "\n\n" "Choose a network to explore pools:"

        await query.message.edit_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )

    except Exception as e:
        logger.error(f"Error fetching networks: {e}", exc_info=True)
        await query.message.edit_text(
            ui.notice("error", "Could not fetch networks", str(e)),
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


async def show_network_menu(update: Update, context: Context, network: str) -> None:
    """Show options for a specific network"""
    network_name = NETWORK_NAMES.get(network, network.title())

    keyboard = [
        [
            InlineKeyboardButton(
                text="🔥 Trending", callback_data=f"dex:gecko_trending_{network}"
            ),
            InlineKeyboardButton(
                text="📈 Top Pools", callback_data=f"dex:gecko_top_{network}"
            ),
        ],
        [
            InlineKeyboardButton(
                text="🆕 New Pools", callback_data=f"dex:gecko_new_{network}"
            ),
        ],
        [
            InlineKeyboardButton(text="« Back", callback_data="dex:gecko_networks"),
        ],
    ]

    message = f"🌐 *{escape_markdown_v2(network_name)}*\n\n" "Select what to explore:"

    await update.callback_query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
