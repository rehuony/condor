"""Pool listings and the pool selection keyboard."""

import logging

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
    format_error_message,
)

from .._shared import (
    format_compact_number,
    get_cached,
)
from ._common import SOLANA_CLMM_NETWORK
from .pool_detail import _show_pool_detail
from .pool_info import _build_balance_table_compact

logger = logging.getLogger(__name__)


async def handle_pool_list(
    update: Update, context: Context, connector: str = "meteora"
) -> None:
    """Handle CLMM pool list for a specific connector"""
    # Get cached gateway balances (cached from /lp menu)
    gateway_data = get_cached(context.user_data, "gateway_balances", ttl=120)
    balance_table = _build_balance_table_compact(gateway_data)

    connector_name = connector.capitalize()
    help_text = (
        rf"📋 *List {connector_name} Pools*"
        + "\n\n"
        + balance_table
        + r"Reply with:"
        + "\n\n"
        r"`[search_term] [limit]`" + "\n\n"
        r"*Examples:*" + "\n"
        r"`SOL 10`" + "\n"
        r"`USDC 5`" + "\n"
    )

    keyboard = [[InlineKeyboardButton(text="✕ Cancel", callback_data="dex:liquidity")]]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    context.user_data["dex_state"] = "pool_list"
    context.user_data["pool_list_connector"] = connector
    # Store message for later editing with results
    context.user_data["pool_list_message_id"] = update.callback_query.message.message_id
    context.user_data["pool_list_chat_id"] = update.callback_query.message.chat.id

    await update.callback_query.message.edit_text(
        help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_pool_list_search(update: Update, context: Context) -> None:
    """Search again on the connector the current results came from"""
    connector = context.user_data.get("pool_list_connector", "meteora")
    await handle_pool_list(update, context, connector=connector)


async def handle_pool_list_orca(update: Update, context: Context) -> None:
    """Handle Orca CLMM pool list"""
    await handle_pool_list(update, context, connector="orca")


def _format_percent(value, decimals: int = 2) -> str:
    """Format percentage value"""
    if value is None:
        return "—"
    try:
        num = float(value)
        if num == 0:
            return "0%"
        if num >= 100:
            return f"{num:.0f}%"
        return f"{num:.{decimals}f}%"
    except (ValueError, TypeError):
        return "—"


def _format_pool_table(pools: list) -> str:
    """Format pools as a compact table optimized for mobile

    Shows: #, Pair, APR%, Bin, Fee, TVL

    Args:
        pools: List of pool data dictionaries

    Returns:
        Formatted table string (not escaped)
    """
    if not pools:
        return "No pools found"

    lines = []

    # Header - balanced for mobile (~40 chars)
    lines.append("```")
    lines.append(f"{'#':>2} {'Pair':<11} {'APR%':>7} {'Bin':>3} {'Fee':>5} {'TVL':>5}")
    lines.append("─" * 40)

    for i, pool in enumerate(pools):
        idx = str(i + 1)
        # Format pair to fit 11 chars - abbreviate base token if needed, keep quote
        full_pair = pool.get("trading_pair", "N/A")
        if "-" in full_pair and len(full_pair) > 11:
            base, quote = full_pair.rsplit("-", 1)
            max_base_len = 11 - len(quote) - 1  # -1 for the dash
            if max_base_len > 2:
                base = base[: max_base_len - 1] + "…"
            pair = f"{base}-{quote}"[:11]
        else:
            pair = full_pair[:11]

        # Get TVL value
        tvl_val = 0
        try:
            tvl_val = float(pool.get("liquidity", 0) or 0)
        except (ValueError, TypeError):
            pass

        # Compact TVL
        tvl = format_compact_number(tvl_val)

        # Base fee percentage - compact
        base_fee = pool.get("base_fee_percentage")
        if base_fee:
            try:
                fee_val = float(base_fee)
                fee_str = f"{fee_val:.2f}" if fee_val < 10 else f"{int(fee_val)}"
            except (ValueError, TypeError):
                fee_str = "—"
        else:
            fee_str = "—"

        # APR percentage - compact format for large values
        apr = pool.get("apr")
        if apr:
            try:
                apr_val = float(apr)
                if apr_val >= 1000000:
                    apr_str = f"{apr_val/1000000:.0f}M"
                elif apr_val >= 10000:
                    apr_str = f"{apr_val/1000:.0f}K"
                elif apr_val >= 1000:
                    apr_str = f"{apr_val/1000:.1f}K"
                elif apr_val >= 100:
                    apr_str = f"{apr_val:.0f}"
                else:
                    apr_str = f"{apr_val:.2f}"
            except (ValueError, TypeError):
                apr_str = "—"
        else:
            apr_str = "—"

        # Bin step
        bin_step = pool.get("bin_step", "—")

        lines.append(
            f"{idx:>2} {pair:<11} {apr_str:>7} {bin_step:>3} {fee_str:>5} {tvl:>5}"
        )

    lines.append("```")

    return "\n".join(lines)


def _build_pool_selection_keyboard(
    pools: list, search_term: str = None, is_pair_search: bool = False
) -> InlineKeyboardMarkup:
    """Build keyboard with numbered buttons for pool selection

    Args:
        pools: List of pools to select from
        search_term: Original search term
        is_pair_search: Whether this is a BASE-QUOTE pair search (e.g., "ORE-SOL")
    """
    keyboard = []

    # Create rows of 5 buttons each for pool selection
    row = []
    for i, pool in enumerate(pools):
        btn = InlineKeyboardButton(
            text=str(i + 1), callback_data=f"dex:pool_select:{i}"
        )
        row.append(btn)
        if len(row) == 5:
            keyboard.append(row)
            row = []

    # Add remaining buttons
    if row:
        keyboard.append(row)

    # Add Plot Liquidity buttons for pair searches (BASE-QUOTE format)
    if is_pair_search and len(pools) > 1:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="📊 Plot Top 75%", callback_data="dex:plot_liquidity:75"
                ),
                InlineKeyboardButton(
                    text="📊 Top 90%", callback_data="dex:plot_liquidity:90"
                ),
                InlineKeyboardButton(
                    text="📊 Top 99%", callback_data="dex:plot_liquidity:99"
                ),
            ]
        )

    # Add search again and back buttons
    keyboard.append(
        [
            InlineKeyboardButton(
                text="🔍 New Search", callback_data="dex:pool_list_search"
            ),
            InlineKeyboardButton(
                text="« Back to LP Menu", callback_data="dex:liquidity"
            ),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def process_pool_list(update: Update, context: Context, user_input: str) -> None:
    """Process pool list (meteora only) or select a pool by number"""
    try:
        user_input = user_input.strip()

        # Check if user is selecting a pool by number
        if user_input.isdigit():
            pool_index = int(user_input) - 1  # Convert to 0-based index
            cached_pools = context.user_data.get("pool_list_cache", [])

            if 0 <= pool_index < len(cached_pools):
                pool = cached_pools[pool_index]

                # Show immediate feedback
                pair = pool.get("trading_pair", pool.get("name", "Pool"))

                # Delete user's message
                try:
                    await update.message.delete()
                except Exception:
                    pass

                # Show loading state
                message_id = context.user_data.get("pool_list_message_id")
                chat_id = context.user_data.get("pool_list_chat_id")
                if message_id and chat_id:
                    try:
                        loading_text = rf"⏳ *Loading pool data\.\.\.*" + "\n\n"
                        loading_text += escape_markdown_v2(f"🏊 {pair}") + "\n"
                        loading_text += r"_Fetching liquidity bins and pool info\.\.\._"
                        await context.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=loading_text,
                            parse_mode="MarkdownV2",
                        )
                    except Exception:
                        pass

                await _show_pool_detail(update, context, pool)
                return
            else:
                raise ValueError(f"Invalid pool number. Choose 1-{len(cached_pools)}")

        # Otherwise, search for pools
        parts = user_input.split()

        # Use connector from context (set by handle_pool_list), default to meteora
        connector = context.user_data.get("pool_list_connector", "meteora")
        search_term = parts[0] if len(parts) > 0 and parts[0] != "_" else None
        # Parse limit from user input (default 15, max 30 for display)
        requested_limit = int(parts[1]) if len(parts) > 1 else 15
        display_limit = min(requested_limit, 30)  # Cap display at 30
        # Request more from API to have enough after filtering
        api_limit = max(requested_limit * 3, 100)

        # Show loading message immediately
        search_info = f" for '{search_term}'" if search_term else ""
        loading_msg = rf"⏳ *Loading pools*{escape_markdown_v2(search_info)}\.\.\."

        # Try to edit existing message or send new one
        message_id = context.user_data.get("pool_list_message_id")
        chat_id = context.user_data.get("pool_list_chat_id")
        loading_sent = None

        if message_id and chat_id:
            try:
                await update.message.delete()
            except Exception:
                pass
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=loading_msg,
                    parse_mode="MarkdownV2",
                )
                loading_sent = "edited"
            except Exception:
                loading_sent = None

        if not loading_sent:
            sent_msg = await update.message.answer(loading_msg, parse_mode="MarkdownV2")
            context.user_data["pool_list_message_id"] = sent_msg.message_id
            context.user_data["pool_list_chat_id"] = sent_msg.chat.id
            chat_id = sent_msg.chat.id  # Ensure chat_id is set
            loading_sent = "new"

        # Ensure chat_id is set for get_client
        if not chat_id:
            chat_id = update.effective_chat.id

        client = await get_client(chat_id, user_data=context.user_data)

        if not hasattr(client, "gateway_clmm"):
            raise ValueError("Gateway CLMM not available")

        result = await client.gateway_clmm.get_pools(
            connector=connector, page=0, limit=api_limit, search_term=search_term
        )

        pools = result.get("pools", [])

        # Add connector/network to each pool for later use
        for pool in pools:
            pool["connector"] = connector
            pool["network"] = SOLANA_CLMM_NETWORK

        if not pools:
            message = escape_markdown_v2("📋 No pools found")
            context.user_data["pool_list_cache"] = []
            keyboard = [
                [
                    InlineKeyboardButton(
                        text="« Back to LP Menu", callback_data="dex:liquidity"
                    )
                ]
            ]
            reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        else:
            # Sort by APR% descending, filter out low TVL pools (< $100)
            active_pools = [
                p for p in pools if float(p.get("liquidity", 0) or 0) >= 100
            ]
            active_pools.sort(key=lambda x: float(x.get("apr", 0) or 0), reverse=True)

            # If no active pools, show all
            display_pools = (
                active_pools[:display_limit] if active_pools else pools[:display_limit]
            )

            # Detect if this is a BASE-QUOTE pair search (e.g., "ORE-SOL", "BTC-USDC")
            is_pair_search = bool(search_term and "-" in search_term)

            # Cache pools for selection (with search term for back navigation)
            context.user_data["pool_list_cache"] = display_pools
            context.user_data["pool_list_search_term"] = search_term
            context.user_data["pool_list_limit"] = display_limit
            context.user_data["pool_list_is_pair_search"] = is_pair_search

            # Use actual pool count (active_pools or pools), not API total which may be inaccurate
            actual_total = len(active_pools) if active_pools else len(pools)
            search_info = f" for '{search_term}'" if search_term else ""
            connector_name = connector.capitalize()

            header = (
                rf"📋 *{connector_name} Pools*{escape_markdown_v2(search_info)} \({len(display_pools)} of {actual_total}\)"
                + "\n\n"
            )

            table = _format_pool_table(display_pools)
            message = header + table + "\n\n_Select pool number:_"

            # Build keyboard with numbered buttons (add Plot Liquidity for pair searches)
            reply_markup = _build_pool_selection_keyboard(
                display_pools, search_term, is_pair_search
            )

        # Results are picked with buttons; a new search re-arms the prompt
        context.user_data["dex_state"] = "pool_list_results"

        # Edit the loading message with results (we already have message_id/chat_id from loading step)
        message_id = context.user_data.get("pool_list_message_id")
        chat_id = context.user_data.get("pool_list_chat_id")

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.warning(f"Failed to edit message, sending new: {e}")
            sent_msg = await update.message.answer(
                message, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
            context.user_data["pool_list_message_id"] = sent_msg.message_id
            context.user_data["pool_list_chat_id"] = sent_msg.chat.id
        ui.remember_menu(
            context.user_data,
            "dex_pool_list",
            context.user_data.get("pool_list_message_id"),
        )

    except Exception as e:
        logger.error(f"Error listing pools: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to list pools: {str(e)}")
        await update.message.answer(error_message, parse_mode="MarkdownV2")


async def handle_pool_list_back(update: Update, context: Context) -> None:
    """Handle back button to return to pool list"""
    cached_pools = context.user_data.get("pool_list_cache", [])
    search_term = context.user_data.get("pool_list_search_term")
    is_pair_search = context.user_data.get("pool_list_is_pair_search", False)

    if not cached_pools:
        # No cached pools, go to search
        await handle_pool_list(update, context)
        return

    # Rebuild the pool list message
    total = len(cached_pools)
    search_info = f" for '{search_term}'" if search_term else ""

    header = rf"📋 *CLMM Pools*{escape_markdown_v2(search_info)} \({total}\)" + "\n\n"
    table = _format_pool_table(cached_pools)
    message = header + table + "\n\n_Select pool number:_"

    reply_markup = _build_pool_selection_keyboard(
        cached_pools, search_term, is_pair_search
    )

    # Results are picked with buttons; a new search re-arms the prompt
    context.user_data["dex_state"] = "pool_list_results"

    # Delete the current message (could be a photo) and send new text message
    chat = update.callback_query.message.chat
    try:
        await update.callback_query.message.delete()
    except Exception:
        pass

    # Send new message with pool list
    sent_msg = await chat.send_message(
        text=message, parse_mode="MarkdownV2", reply_markup=reply_markup
    )

    # Store message ID for future navigation
    context.user_data["pool_list_message_id"] = sent_msg.message_id
    context.user_data["pool_list_chat_id"] = chat.id
    ui.remember_menu(context.user_data, "dex_pool_list", sent_msg.message_id)
