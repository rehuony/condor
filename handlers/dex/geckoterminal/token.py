"""Token info, token search and add-token actions."""

import logging

from condor.pool_data import (
    gecko_call,
)
from condor.telegram.types import (
    NO_LINK_PREVIEW,
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from utils import telegram_ui as ui
from utils.telegram_formatters import escape_markdown_v2, format_error_message

from ._format import (
    NETWORK_NAMES,
    _extract_pools_from_response,
    _format_pool_line,
    _format_price,
    _format_volume,
    _get_pool_symbols,
)
from .state import set_gecko_network

logger = logging.getLogger(__name__)


async def handle_gecko_token_info(
    update: Update, context: Context, token_type: str
) -> None:
    """Show detailed token info using get_specific_token_on_network"""
    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    # Get token info based on type
    if token_type == "base":
        token_address = pool_data.get("base_token_address", "")
        token_symbol = pool_data.get("base_token_symbol", "Unknown")
    else:
        token_address = pool_data.get("quote_token_address", "")
        token_symbol = pool_data.get("quote_token_symbol", "Unknown")

    network = pool_data.get("network", "")

    if not token_address or not network:
        await query.answer("Token address not available")
        return

    await query.answer("Loading token info...")

    # Show loading
    await query.message.edit_text(
        f"🪙 *Loading {escape_markdown_v2(token_symbol)} info\\.\\.\\.*",
        parse_mode="MarkdownV2",
    )

    try:
        result = await gecko_call(
            "get_specific_token_on_network", network, token_address
        )

        # Extract token data
        token_data = {}
        if isinstance(result, dict):
            data = result.get("data", result)
            if isinstance(data, dict):
                token_data = data.get("attributes", data)
            elif isinstance(data, list) and data:
                token_data = data[0].get("attributes", data[0])
        elif hasattr(result, "data"):
            attrs = getattr(result.data, "attributes", result.data)
            if hasattr(attrs, "__dict__"):
                token_data = attrs.__dict__
            else:
                token_data = attrs

        # Check for pandas DataFrame
        try:
            import pandas as pd

            if isinstance(result, pd.DataFrame) and not result.empty:
                token_data = result.to_dict("records")[0]
        except ImportError:
            pass

        if not token_data:
            await query.message.edit_text(
                f"❌ *Token Info Not Found*\n\n"
                f"Could not fetch info for `{escape_markdown_v2(token_address[:20])}...`",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="« Back to Pool",
                                callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}",
                            )
                        ]
                    ]
                ),
            )
            return

        # Store token data for add-to-gateway
        context.user_data["gecko_current_token"] = {
            "address": token_address,
            "symbol": token_data.get("symbol", token_symbol),
            "name": token_data.get("name", ""),
            "decimals": token_data.get("decimals"),
            "network": network,
        }

        # Build token info display
        name = token_data.get("name", "Unknown")
        symbol = token_data.get("symbol", token_symbol)
        decimals = token_data.get("decimals", "N/A")

        lines = [
            f"🪙 *{escape_markdown_v2(symbol)}* \\- {escape_markdown_v2(name)}\n",
            f"🌐 Network: {escape_markdown_v2(NETWORK_NAMES.get(network, network))}",
        ]

        # Price info
        price_usd = token_data.get("price_usd")
        if price_usd:
            try:
                price = float(price_usd)
                lines.append(f"💰 Price: {escape_markdown_v2(_format_price(price))}")
            except (ValueError, TypeError):
                pass

        # Market data
        fdv = token_data.get("fdv_usd")
        if fdv:
            try:
                lines.append(
                    f"📈 FDV: {escape_markdown_v2(_format_volume(float(fdv)))}"
                )
            except (ValueError, TypeError):
                pass

        market_cap = token_data.get("market_cap_usd")
        if market_cap:
            try:
                lines.append(
                    f"💎 Market Cap: {escape_markdown_v2(_format_volume(float(market_cap)))}"
                )
            except (ValueError, TypeError):
                pass

        total_supply = token_data.get("total_supply")
        if total_supply:
            try:
                lines.append(
                    f"📊 Total Supply: {escape_markdown_v2(_format_volume(float(total_supply)))}"
                )
            except (ValueError, TypeError):
                pass

        # Volume
        vol_24h = token_data.get("volume_usd", {})
        if isinstance(vol_24h, dict):
            h24 = vol_24h.get("h24")
        else:
            h24 = vol_24h
        if h24:
            try:
                lines.append(
                    f"📈 Vol 24h: {escape_markdown_v2(_format_volume(float(h24)))}"
                )
            except (ValueError, TypeError):
                pass

        lines.append("")
        lines.append(f"🔢 Decimals: {escape_markdown_v2(str(decimals))}")
        lines.append(f"\n📍 Address:\n`{token_address}`")

        # GeckoTerminal link
        gecko_url = f"https://www.geckoterminal.com/{network}/tokens/{token_address}"
        lines.append(f"\n🦎 [View on GeckoTerminal]({escape_markdown_v2(gecko_url)})")

        # Build keyboard
        keyboard = [
            [
                InlineKeyboardButton(
                    text="🔍 Search Pools", callback_data="dex:gecko_token_search"
                ),
                InlineKeyboardButton(
                    text="➕ Add to Gateway", callback_data="dex:gecko_token_add"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="« Back to Pool",
                    callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}",
                ),
            ],
        ]

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            link_preview_options=NO_LINK_PREVIEW,
        )

    except Exception as e:
        logger.error(f"Error fetching token info: {e}", exc_info=True)
        await query.message.edit_text(
            format_error_message(str(e)),
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


async def handle_gecko_token_search(update: Update, context: Context) -> None:
    """Search for pools using the current token"""
    query = update.callback_query

    token_info = context.user_data.get("gecko_current_token")
    if not token_info:
        await query.answer("No token selected")
        return

    # Set the network and trigger search with the token address
    network = token_info.get("network", "solana")
    set_gecko_network(context.user_data, network)

    # Store token address for search
    context.user_data["gecko_search_token"] = token_info.get("address", "")
    context.user_data["gecko_view"] = "search"

    # Simulate search
    await query.answer("Searching pools...")

    try:
        result = await gecko_call(
            "get_top_pools_by_network_token", network, token_info.get("address", "")
        )
        pools = _extract_pools_from_response(result, 10)

        if not pools:
            await query.message.edit_text(
                r"❌ *No pools found*" + "\n\n"
                f"No pools found for this token on {escape_markdown_v2(NETWORK_NAMES.get(network, network))}\\.",
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
            return

        # Store pools for selection
        context.user_data["gecko_pools"] = pools
        context.user_data["gecko_network"] = network

        # Build message
        symbol = token_info.get("symbol", "Token")
        lines = [f"🔍 *Pools for {escape_markdown_v2(symbol)}*\n"]

        for i, pool in enumerate(pools, 1):
            line = _format_pool_line(pool, i)
            lines.append(escape_markdown_v2(line))

        lines.append("\n_Select a pool for details:_")

        # Build keyboard
        keyboard = []
        row = []
        for i, pool in enumerate(pools):
            base, quote_sym = _get_pool_symbols(pool)
            btn = InlineKeyboardButton(
                text=f"{i+1}. {base[:6]}/{quote_sym[:4]}",
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
                InlineKeyboardButton(text="« Back", callback_data="dex:gecko_explore"),
            ]
        )

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )

    except Exception as e:
        logger.error(f"Error searching token pools: {e}", exc_info=True)
        await query.message.edit_text(
            format_error_message(str(e)),
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


async def handle_gecko_token_add(update: Update, context: Context) -> None:
    """Add token to Gateway"""
    query = update.callback_query

    token_info = context.user_data.get("gecko_current_token")
    if not token_info:
        await query.answer("No token selected")
        return

    # Map GeckoTerminal network to Gateway network
    network_mapping = {
        "solana": "solana-mainnet-beta",
        "eth": "ethereum-mainnet",
        "base": "base-mainnet",
        "arbitrum": "arbitrum-one",
        "bsc": "bsc-mainnet",
        "polygon_pos": "polygon-mainnet",
    }

    gecko_network = token_info.get("network", "solana")
    gateway_network = network_mapping.get(gecko_network, gecko_network)
    symbol = token_info.get("symbol", "")
    name = token_info.get("name", "")
    address = token_info.get("address", "")
    decimals = token_info.get("decimals")

    # If decimals is missing, we need to prompt or use default
    if decimals is None:
        # Default decimals based on network
        if gecko_network == "solana":
            decimals = 9  # Most SPL tokens
        else:
            decimals = 18  # Most ERC20 tokens

    await query.answer("Adding token to Gateway...")

    try:
        from config_manager import get_config_manager

        client = await get_config_manager().get_client()
        await client.gateway.add_token(
            network_id=gateway_network,
            address=address,
            symbol=symbol,
            decimals=decimals,
            name=name if name else None,
        )

        success_text = (
            f"✅ *Token Added*\n\n"
            f"*{escape_markdown_v2(symbol)}* added to {escape_markdown_v2(gateway_network)}\n\n"
            "⚠️ _Restart Gateway for changes to take effect\\._"
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    text="« Back to Pool",
                    callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}",
                )
            ],
        ]

        await query.message.edit_text(
            success_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )

    except Exception as e:
        logger.error(f"Error adding token to Gateway: {e}", exc_info=True)

        error_msg = str(e)
        if "already exists" in error_msg.lower():
            error_text = f"⚠️ *Token Already Exists*\n\n{escape_markdown_v2(symbol)} is already in Gateway\\."
        else:
            error_text = ui.notice("error", "Could not add the token", error_msg)

        await query.message.edit_text(
            error_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="« Back to Pool",
                            callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}",
                        )
                    ]
                ]
            ),
        )
