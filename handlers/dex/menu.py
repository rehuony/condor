"""
DEX Trading main menu

Provides:
- Main DEX trading menu display with balances and positions
- Close functionality
"""

import asyncio
import logging

from condor.preferences import get_all_enabled_networks, get_dex_last_swap
from condor.telegram.errors import is_not_modified
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from config_manager import get_client
from utils.telegram_formatters import (
    KNOWN_TOKENS,
    escape_markdown_v2,
    resolve_token_symbol,
)

from ._shared import build_token_cache, cached_call, format_usd_value, invalidate_cache

logger = logging.getLogger(__name__)

# The background loading task per user. Module state rather than
# ``user_data``: an ``asyncio.Task`` in ``user_data`` reaches the persistence
# snapshot, and a Task cannot be pickled.
_loading_tasks: dict[int, asyncio.Task] = {}


def _loading_owner(context: Context) -> int:
    return context.user_data.get("_user_id") or id(context.user_data)


def cancel_dex_loading_task(context: Context) -> None:
    """Cancel any pending DEX menu loading task"""
    task = _loading_tasks.pop(_loading_owner(context), None)
    if task and not task.done():
        task.cancel()
        logger.debug("Cancelled pending DEX menu loading task")


def _format_amount(value: float) -> str:
    """Format token amounts with appropriate precision"""
    if value == 0:
        return "0"
    elif abs(value) < 0.0001:
        return f"{value:.2e}"
    elif abs(value) < 1:
        return f"{value:.6f}".rstrip("0").rstrip(".")
    else:
        formatted = f"{value:.4f}".rstrip("0").rstrip(".")
        return formatted if "." in f"{value:.4f}" else f"{value:.0f}"


def _format_position_line(pos: dict, token_cache: dict = None) -> str:
    """Format a single LP position line with resolved token symbols"""
    token_cache = token_cache or {}
    connector = pos.get("connector", "unknown")

    # Resolve token addresses to symbols
    base_token = pos.get("base_token", pos.get("token_a", ""))
    quote_token = pos.get("quote_token", pos.get("token_b", ""))
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)
    pair = f"{base_symbol}-{quote_symbol}"

    # Get current amounts
    amount_a = pos.get(
        "base_token_amount", pos.get("amount_a", pos.get("token_a_amount", 0))
    )
    amount_b = pos.get(
        "quote_token_amount", pos.get("amount_b", pos.get("token_b_amount", 0))
    )

    # Get price range
    lower = pos.get("lower_price", pos.get("price_lower", ""))
    upper = pos.get("upper_price", pos.get("price_upper", ""))

    # Get in-range status
    in_range = pos.get("in_range", "")
    status_emoji = (
        "🟢" if in_range == "IN_RANGE" else "🔴" if in_range == "OUT_OF_RANGE" else ""
    )

    # Format range
    if lower and upper:
        range_str = f"[{_format_price(lower)}-{_format_price(upper)}]"
    else:
        range_str = ""

    return f"  • {pair} ({connector}): {status_emoji} {range_str}"


def _format_price(price) -> str:
    """Format price for display"""
    try:
        num = float(price)
        if num >= 1:
            return f"{num:.2f}"
        elif num >= 0.0001:
            return f"{num:.4f}"
        else:
            return f"{num:.6f}"
    except (ValueError, TypeError):
        return str(price)


async def _fetch_balances(client, refresh: bool = False) -> dict:
    """Fetch gateway/DEX balances (blockchain wallets like solana, ethereum)

    Args:
        client: The API client
        refresh: If True, force refresh from exchanges. If False, use cached state (default)
    """
    from collections import defaultdict

    data = {
        "balances_by_network": defaultdict(list),
        "total_value": 0,
    }

    try:
        if not hasattr(client, "portfolio"):
            logger.warning("Client has no portfolio attribute")
            return data

        # Fetch available gateway networks dynamically
        gateway_networks = set()
        if hasattr(client, "gateway"):
            try:
                networks_response = await client.gateway.list_networks()
                networks = networks_response.get("networks", [])
                # Networks come as "chain-network" format (e.g., "solana-mainnet-beta")
                for network in networks:
                    if isinstance(network, dict):
                        network_id = network.get("network_id", str(network))
                    else:
                        network_id = str(network)
                    gateway_networks.add(network_id.lower())
                logger.debug(f"Gateway networks available: {gateway_networks}")
            except Exception as e:
                logger.debug(f"Could not fetch gateway networks: {e}")

        result = await client.portfolio.get_state(refresh=refresh)
        if not result:
            logger.info("Portfolio get_state returned empty result")
            return data

        logger.info(f"Processing portfolio state with {len(result)} accounts")
        for account_name, account_data in result.items():
            logger.info(
                f"Account: {account_name}, connectors: {list(account_data.keys())}"
            )
            for connector_name, balances in account_data.items():
                connector_lower = connector_name.lower()

                # Only include gateway/blockchain connectors
                # Check if connector matches any known gateway network
                is_gateway = False
                if gateway_networks:
                    # Match connector name against known gateway networks
                    # e.g., "solana_mainnet-beta" should match "solana-mainnet-beta"
                    connector_normalized = connector_lower.replace("_", "-")
                    is_gateway = connector_normalized in gateway_networks or any(
                        connector_normalized.startswith(net.split("-")[0])
                        for net in gateway_networks
                    )
                else:
                    # Fallback: assume connector names containing chain names are gateway connectors
                    # This handles cases where gateway.list_networks() fails
                    is_gateway = any(
                        chain in connector_lower
                        for chain in [
                            "solana",
                            "ethereum",
                            "polygon",
                            "arbitrum",
                            "base",
                            "avalanche",
                            "optimism",
                        ]
                    )

                if not is_gateway:
                    logger.debug(f"Skipping non-gateway connector: {connector_name}")
                    continue

                if balances:
                    # Use connector name as network identifier
                    network = connector_lower
                    logger.info(
                        f"Processing gateway connector: {connector_name}, balances: {len(balances)}"
                    )

                    for balance in balances:
                        token = balance.get("token", "???")
                        units = balance.get("units", 0)
                        value = balance.get("value", 0)
                        logger.debug(
                            f"  Token: {token}, units: {units}, value: {value}"
                        )
                        if value > 0.01:
                            data["balances_by_network"][network].append(
                                {"token": token, "units": units, "value": value}
                            )
                            data["total_value"] += value

        # Calculate percentages and sort
        for network in data["balances_by_network"]:
            for balance in data["balances_by_network"][network]:
                balance["percentage"] = (
                    (balance["value"] / data["total_value"] * 100)
                    if data["total_value"] > 0
                    else 0
                )
            data["balances_by_network"][network].sort(
                key=lambda x: x["value"], reverse=True
            )

        logger.info(
            f"Gateway balances: {len(data['balances_by_network'])} networks, total: ${data['total_value']:.2f}"
        )

    except Exception as e:
        logger.error(f"Error fetching balances: {e}", exc_info=True)

    return data


def _filter_balances_by_networks(balances_data: dict, enabled_networks: set) -> dict:
    """Filter balances data to only include enabled networks.

    Args:
        balances_data: Dict with balances_by_network and total_value
        enabled_networks: Set of enabled network IDs, or None for no filtering

    Returns:
        Filtered balances data with recalculated total_value and percentages
    """
    if enabled_networks is None or not balances_data:
        return balances_data

    balances_by_network = balances_data.get("balances_by_network", {})
    if not balances_by_network:
        return balances_data

    # Filter networks
    filtered_networks = {
        network: balances
        for network, balances in balances_by_network.items()
        if network in enabled_networks
    }

    # Recalculate total value
    total_value = sum(
        bal["value"] for balances in filtered_networks.values() for bal in balances
    )

    # Recalculate percentages
    for balances in filtered_networks.values():
        for bal in balances:
            bal["percentage"] = (
                (bal["value"] / total_value * 100) if total_value > 0 else 0
            )

    return {
        "balances_by_network": filtered_networks,
        "total_value": total_value,
    }


async def _fetch_lp_positions(client) -> dict:
    """Fetch LP positions only"""
    data = {"lp_positions": [], "token_cache": dict(KNOWN_TOKENS)}

    try:
        if not hasattr(client, "gateway_clmm"):
            return data

        result = await client.gateway_clmm.search_positions(
            limit=100, offset=0, status="OPEN"
        )

        if not result:
            return data

        positions = result.get("data", [])
        logger.info(f"LP positions API returned {len(positions)} positions")
        for p in positions[:5]:
            logger.info(
                f"  Position: {p.get('trading_pair')} status={p.get('status')} liquidity={p.get('liquidity')}"
            )

        # Filter to only show OPEN positions
        open_positions = [p for p in positions if p.get("status") == "OPEN"]
        logger.info(f"After OPEN filter: {len(open_positions)} positions")

        # Filter out positions with 0 liquidity. hapi's position rows carry the
        # amounts as base_token_amount / quote_token_amount (always present,
        # always floats) — there is no 'liquidity' field to read.
        def has_liquidity(pos):
            base = float(pos.get("base_token_amount") or 0)
            quote = float(pos.get("quote_token_amount") or 0)
            return base > 0 or quote > 0

        active_positions = [p for p in open_positions if has_liquidity(p)]
        if len(active_positions) < len(open_positions):
            logger.info(
                f"Filtered {len(open_positions) - len(active_positions)} positions with 0 liquidity"
            )

        data["lp_positions"] = active_positions[:5]

        # Fetch tokens for LP position networks
        data["token_cache"] = await build_token_cache(
            client,
            (pos.get("network", "solana-mainnet-beta") for pos in active_positions),
        )

    except Exception as e:
        logger.error(f"Error fetching LP positions: {e}", exc_info=True)

    return data


def _build_menu_keyboard() -> InlineKeyboardMarkup:
    """Build the DEX menu keyboard"""
    keyboard = [
        [
            InlineKeyboardButton(text="💱 Swap", callback_data="dex:swap"),
            InlineKeyboardButton(
                text="💧 Liquidity Pools", callback_data="dex:liquidity"
            ),
        ],
        [
            InlineKeyboardButton(text="🔄 Refresh", callback_data="dex:refresh"),
            InlineKeyboardButton(text="✖️ Close", callback_data="dex:close"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _build_loading_message(server_name: str = None) -> str:
    """Build the initial loading message"""
    if server_name:
        title = f"🔄 *DEX Trading* \\| _Server: {escape_markdown_v2(server_name)}_"
    else:
        title = r"🔄 *DEX Trading*"

    return (
        title + "\n\n"
        r"💰 *Gateway Balances:*" + "\n"
        r"⏳ _Loading\.\.\._" + "\n\n"
        "Select operation:"
    )


def _build_menu_with_data(
    gateway_data: dict, last_swap: dict = None, server_name: str = None
) -> str:
    """Build the menu message with gateway data"""
    if server_name:
        header = f"🔄 *DEX Trading* \\| _Server: {escape_markdown_v2(server_name)}_\n\n"
    else:
        header = r"🔄 *DEX Trading*" + "\n\n"

    # Show gateway balances organized by network if available
    if (
        gateway_data.get("balances_by_network")
        and len(gateway_data["balances_by_network"]) > 0
    ):
        header += r"💰 *Gateway Balances:*" + "\n\n"

        # Display each network with its balances in a table
        for network, balances in gateway_data["balances_by_network"].items():
            # Calculate network total
            network_total = sum(bal["value"] for bal in balances)
            network_total_str = format_usd_value(network_total)

            header += f"  🌐 *{escape_markdown_v2(network.upper())}* \\- `{escape_markdown_v2(network_total_str)}`\n\n"

            # Create table for this network's balances
            table = "```\n"
            table += f"{'Token':<10} {'Amount':<12} {'Value':<10} {'%':>6}\n"
            table += f"{'─'*10} {'─'*12} {'─'*10} {'─'*6}\n"

            # Show top 5 tokens per network
            for bal in balances[:5]:
                token = bal["token"][:9]  # Truncate if needed
                units_str = _format_amount(bal["units"])[:11]
                value_str = format_usd_value(bal["value"]).replace("$", "")[:9]
                pct = bal["percentage"]

                table += f"{token:<10} {units_str:<12} {value_str:<10} {pct:>5.1f}%\n"

            table += "```\n"
            header += table

        # Show total portfolio value
        if gateway_data["total_value"] > 0:
            total_str = format_usd_value(gateway_data["total_value"])
            header += f"💵 *Total:* `{escape_markdown_v2(total_str)}`\n\n"

    # Show active LP positions if available
    if gateway_data.get("lp_positions"):
        header += r"📍 *Active LP Positions:*" + "\n"
        token_cache = gateway_data.get("token_cache", {})
        for pos in gateway_data["lp_positions"]:
            line = _format_position_line(pos, token_cache=token_cache)
            header += escape_markdown_v2(line) + "\n"
        header += "\n"

    # Show last swap info as fallback if no balances
    if (
        not gateway_data.get("balances_by_network")
        and last_swap
        and "connector" in last_swap
    ):
        header += r"⚡ *Last Swap:*" + "\n"
        header += f"• Connector: {escape_markdown_v2(last_swap['connector'])}\n"
        if "trading_pair" in last_swap:
            header += f"• Pair: {escape_markdown_v2(last_swap['trading_pair'])}\n"
        header += "\n"

    header += "Select operation:"
    return header


async def _load_menu_data_background(
    message,
    context: Context,
    reply_markup,
    last_swap,
    server_name: str = None,
    refresh: bool = False,
    chat_id: int = None,
) -> None:
    """Background task to load gateway data and update the menu progressively.

    This runs as a background task so users can navigate away without waiting.
    Handles cancellation gracefully.

    Args:
        refresh: If True, force refresh balances from exchanges (bypasses 5-min API cache)
        chat_id: Chat ID for per-chat server selection
    """
    gateway_data = {
        "balances_by_network": {},
        "lp_positions": [],
        "total_value": 0,
        "token_cache": {},
    }

    try:
        client = await get_client(chat_id, user_data=context.user_data)

        # Step 2: Fetch balances first (usually fast) and update UI immediately
        # When refresh=True, bypass local cache and tell API to refresh from exchanges
        if refresh:
            # Direct call without caching, with refresh=True for API
            balances_data = await _fetch_balances(client, refresh=True)
        else:
            balances_data = await cached_call(
                context.user_data, "gateway_balances", _fetch_balances, 60, client
            )

        # Filter by enabled networks from wallet preferences
        enabled_networks = get_all_enabled_networks(context.user_data)
        if enabled_networks:
            logger.info(
                f"Filtering DEX balances by enabled networks: {enabled_networks}"
            )
            balances_data = _filter_balances_by_networks(
                balances_data, enabled_networks
            )

        gateway_data["balances_by_network"] = balances_data.get(
            "balances_by_network", {}
        )
        gateway_data["total_value"] = balances_data.get("total_value", 0)

        # Update UI with balances (show "Loading positions..." for LP)
        if gateway_data["balances_by_network"]:
            balances_message = _build_menu_with_data(
                gateway_data, last_swap, server_name
            )
            # Add loading indicator for positions
            balances_message = balances_message.replace(
                "Select operation:",
                "_Loading LP positions\\.\\.\\._\n\nSelect operation:",
            )
            try:
                await message.edit_text(
                    balances_message, parse_mode="MarkdownV2", reply_markup=reply_markup
                )
            except Exception:
                pass

        # Step 3: Fetch LP positions (can be slower)
        lp_data = await cached_call(
            context.user_data, "gateway_lp_positions", _fetch_lp_positions, 60, client
        )

        gateway_data["lp_positions"] = lp_data.get("lp_positions", [])
        gateway_data["token_cache"] = lp_data.get("token_cache", {})

        logger.debug(
            f"Gateway data: {len(gateway_data.get('balances_by_network', {}))} networks, {len(gateway_data.get('lp_positions', []))} LP positions"
        )

    except asyncio.CancelledError:
        logger.debug("Menu data loading was cancelled (user navigated away)")
        return
    except Exception as e:
        logger.warning(f"Could not fetch gateway data for menu: {e}")

    # Step 4: Final update with all data
    final_message = _build_menu_with_data(gateway_data, last_swap, server_name)

    try:
        await message.edit_text(
            final_message, parse_mode="MarkdownV2", reply_markup=reply_markup
        )
    except asyncio.CancelledError:
        logger.debug("Menu data loading was cancelled during final update")
    except Exception as e:
        # Ignore "message is not modified" errors
        if not is_not_modified(e):
            logger.warning(f"Failed to update menu with data: {e}")
    finally:
        # Clean up task reference
        owner = _loading_owner(context)
        if _loading_tasks.get(owner) is asyncio.current_task():
            _loading_tasks.pop(owner, None)


async def show_dex_menu(
    update: Update, context: Context, refresh: bool = False
) -> None:
    """Display main DEX trading menu with balances and positions

    Uses progressive loading: shows menu immediately, then loads data in background.
    User can navigate away without waiting for data to load.

    Args:
        refresh: If True, force refresh balances from exchanges (bypasses API cache)
    """
    from config_manager import get_config_manager

    # Cancel any existing loading task first
    cancel_dex_loading_task(context)

    # Get server name for display
    server_name = get_config_manager().default_server or "unknown"

    reply_markup = _build_menu_keyboard()

    # Step 1: Show menu immediately with loading indicator
    loading_message = _build_loading_message(server_name)

    if update.callback_query:
        query_message = update.callback_query.message

        # Check if the current message is a photo (can't edit_text on photos)
        if query_message.photo:
            # Delete the photo message and send a new text message
            try:
                await query_message.delete()
            except Exception:
                pass
            message = await query_message.answer(
                loading_message, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
        else:
            # Regular text message - edit it
            try:
                await query_message.edit_text(
                    loading_message, parse_mode="MarkdownV2", reply_markup=reply_markup
                )
                message = query_message
            except Exception as e:
                if not is_not_modified(e):
                    logger.warning(f"Failed to edit menu message: {e}")
                message = query_message
    else:
        message = await update.message.answer(
            loading_message, parse_mode="MarkdownV2", reply_markup=reply_markup
        )

    last_swap = get_dex_last_swap(context.user_data)

    # Spawn background task to load data - user can navigate away without waiting
    chat_id = update.effective_chat.id
    task = asyncio.create_task(
        _load_menu_data_background(
            message,
            context,
            reply_markup,
            last_swap,
            server_name,
            refresh=refresh,
            chat_id=chat_id,
        )
    )
    _loading_tasks[_loading_owner(context)] = task


async def handle_close(update: Update, context: Context) -> None:
    """Handle close button - delete the menu message"""
    # Cancel any pending loading task
    cancel_dex_loading_task(context)

    query = update.callback_query
    try:
        await query.message.delete()
    except Exception as e:
        logger.warning(f"Failed to delete message: {e}")
        await query.answer("Menu closed")


async def handle_refresh(update: Update, context: Context) -> None:
    """Handle refresh button - clear local cache and force refresh from exchanges"""
    query = update.callback_query
    await query.answer("Refreshing from exchanges...")

    # Invalidate all local balance, position, and token caches
    invalidate_cache(context.user_data, "balances", "positions", "tokens")

    # Re-show the menu with fresh data (refresh=True forces API to fetch from exchanges)
    await show_dex_menu(update, context, refresh=True)
