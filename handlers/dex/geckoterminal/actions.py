"""Pool actions: copy address, add liquidity, swap, info."""

import logging

from condor.pool_data import (
    can_fetch_liquidity,
)
from condor.telegram.types import (
    NO_LINK_PREVIEW,
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from utils.telegram_formatters import escape_markdown_v2

from ._format import NETWORK_NAMES, _format_change, _format_price, _format_volume
from .state import GECKO_TO_GATEWAY_NETWORK, NETWORK_DEFAULT_CONNECTOR

logger = logging.getLogger(__name__)


async def handle_copy_address(update: Update, context: Context) -> None:
    """Send pool address as a copyable message"""
    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    address = pool_data.get("address", "N/A")
    name = pool_data.get("name", "Pool")

    await query.answer()
    # Send address as monospace - easy to tap and copy
    await query.message.answer(
        f"📋 *{escape_markdown_v2(name)}*\n\n`{address}`", parse_mode="MarkdownV2"
    )


async def handle_gecko_add_liquidity(update: Update, context: Context) -> None:
    """Handle add liquidity from GeckoTerminal pool detail - bridges to pools.py flow"""
    from condor.pool_data import get_connector_for_dex

    from ..pools import _show_pool_detail

    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    dex_id = pool_data.get("dex_id", "")
    network = pool_data.get("network", "")

    # Verify DEX supports liquidity
    if not can_fetch_liquidity(dex_id, network):
        await query.answer(f"Liquidity not supported for {dex_id}")
        return

    await query.answer("Loading pool for liquidity...")

    # Get the connector name (e.g., "meteora", "raydium", "orca")
    connector = get_connector_for_dex(dex_id)
    if not connector:
        await query.answer(f"Unknown connector for {dex_id}")
        return

    # Convert GeckoTerminal pool data to pools.py format
    pool = {
        "pool_address": pool_data.get("address", ""),
        "address": pool_data.get("address", ""),
        "connector": connector,
        "network": "solana-mainnet-beta",  # Currently all supported DEXes are Solana
        "trading_pair": pool_data.get("name", ""),
        "name": pool_data.get("name", ""),
        "mint_x": pool_data.get("base_token_address", ""),
        "mint_y": pool_data.get("quote_token_address", ""),
        "base_token_symbol": pool_data.get("base_token_symbol", ""),
        "quote_token_symbol": pool_data.get("quote_token_symbol", ""),
        # Pool metrics from GeckoTerminal
        "liquidity": pool_data.get("reserve_usd"),
        "volume_24h": pool_data.get("volume_24h"),
    }

    # Store for pools.py flow
    context.user_data["selected_pool"] = pool
    context.user_data["selected_pool_info"] = {}  # Will be fetched by _show_pool_detail
    context.user_data["add_position_params"] = {
        "connector": connector,
        "network": "solana-mainnet-beta",
        "pool_address": pool_data.get("address", ""),
    }

    # Store for back navigation
    context.user_data["gecko_add_liquidity_source"] = True

    # Delete the current message and show the pool detail with add liquidity controls
    try:
        if getattr(query.message, "photo", None):
            await query.message.delete()
        else:
            await query.message.delete()
    except Exception:
        pass

    # Call pools.py flow
    await _show_pool_detail(update, context, pool, from_callback=True)


async def handle_gecko_swap(update: Update, context: Context) -> None:
    """Set up swap params from GeckoTerminal pool and redirect to swap menu"""
    from ..swap import show_swap_menu

    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    await query.answer("Opening swap...")

    # Get trading pair from pool
    base_symbol = pool_data.get("base_token_symbol", "")
    quote_symbol = pool_data.get("quote_token_symbol", "")

    if not base_symbol or not quote_symbol:
        await query.answer("Token symbols not available")
        return

    # Format trading pair as BASE-QUOTE
    trading_pair = f"{base_symbol}-{quote_symbol}"

    # Map GeckoTerminal network to Gateway network
    gecko_network = pool_data.get("network", "solana")
    gateway_network = GECKO_TO_GATEWAY_NETWORK.get(gecko_network, "solana-mainnet-beta")

    # Get default connector for this network
    connector = NETWORK_DEFAULT_CONNECTOR.get(gecko_network, "jupiter")

    # Set up swap params
    context.user_data["swap_params"] = {
        "connector": connector,
        "network": gateway_network,
        "trading_pair": trading_pair,
        "side": "BUY",
        "amount": "1.0",
        # No slippage: the connector's configured slippage applies until the user overrides it
    }

    context.user_data["dex_state"] = "swap"

    # Store source for back navigation
    context.user_data["swap_from_gecko"] = True
    context.user_data["swap_gecko_pool_index"] = context.user_data.get(
        "gecko_selected_pool_index", 0
    )

    # Delete current message (might be a photo)
    try:
        await query.message.delete()
    except Exception:
        pass

    # Show swap menu - send_new=True since we deleted the message
    await show_swap_menu(update, context, send_new=True)


async def show_gecko_info(update: Update, context: Context) -> None:
    """Show detailed pool info as text (accessed via Info button)"""
    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    await query.answer()

    pool_index = context.user_data.get("gecko_selected_pool_index", 0)
    network_name = NETWORK_NAMES.get(pool_data["network"], pool_data["network"])

    lines = [
        f"📊 *{escape_markdown_v2(pool_data['name'])}*\n",
        f"🌐 Network: {escape_markdown_v2(network_name)}",
        f"🏦 DEX: {escape_markdown_v2(pool_data['dex_id'])}",
        "",
        r"💰 *Price Info:*",
    ]

    if pool_data.get("base_token_price_usd"):
        try:
            price = float(pool_data["base_token_price_usd"])
            symbol = escape_markdown_v2(pool_data["base_token_symbol"])
            lines.append(f"• {symbol}: {escape_markdown_v2(_format_price(price))}")
        except (ValueError, TypeError):
            pass

    if pool_data.get("quote_token_price_usd"):
        try:
            price = float(pool_data["quote_token_price_usd"])
            symbol = escape_markdown_v2(pool_data["quote_token_symbol"])
            lines.append(f"• {symbol}: {escape_markdown_v2(_format_price(price))}")
        except (ValueError, TypeError):
            pass

    lines.append("")
    lines.append(r"📈 *Price Changes:*")

    for period, key in [
        ("1h", "price_change_1h"),
        ("6h", "price_change_6h"),
        ("24h", "price_change_24h"),
    ]:
        change = pool_data.get(key)
        if change is not None:
            try:
                change = float(change)
                lines.append(
                    f"• {period}: {escape_markdown_v2(_format_change(change))}"
                )
            except (ValueError, TypeError):
                pass

    lines.append("")
    lines.append(r"📊 *Volume:*")

    for period, key in [
        ("1h", "volume_1h"),
        ("6h", "volume_6h"),
        ("24h", "volume_24h"),
    ]:
        vol = pool_data.get(key)
        if vol:
            try:
                vol = float(vol)
                lines.append(f"• {period}: {escape_markdown_v2(_format_volume(vol))}")
            except (ValueError, TypeError):
                pass

    # Market cap and FDV
    lines.append("")
    if pool_data.get("market_cap_usd"):
        try:
            mc = float(pool_data["market_cap_usd"])
            lines.append(f"💎 Market Cap: {escape_markdown_v2(_format_volume(mc))}")
        except (ValueError, TypeError):
            pass

    if pool_data.get("fdv_usd"):
        try:
            fdv = float(pool_data["fdv_usd"])
            lines.append(f"📈 FDV: {escape_markdown_v2(_format_volume(fdv))}")
        except (ValueError, TypeError):
            pass

    if pool_data.get("reserve_usd"):
        try:
            reserve = float(pool_data["reserve_usd"])
            lines.append(f"💧 Liquidity: {escape_markdown_v2(_format_volume(reserve))}")
        except (ValueError, TypeError):
            pass

    # Transactions
    txns = pool_data.get("transactions_24h", {})
    if txns:
        buys = txns.get("buys", 0)
        sells = txns.get("sells", 0)
        if buys or sells:
            lines.append("")
            lines.append(r"🔄 *24h Transactions:*")
            lines.append(f"• Buys: {buys} • Sells: {sells}")

    # Pool address and link
    lines.append("")
    addr = pool_data.get("address", "")
    network = pool_data.get("network", "")
    if addr:
        lines.append(f"📍 Address:\n`{addr}`")
        if network:
            gecko_url = f"https://www.geckoterminal.com/{network}/pools/{addr}"
            lines.append(
                f"\n🦎 [View on GeckoTerminal]({escape_markdown_v2(gecko_url)})"
            )

    # Token info buttons
    base_addr = pool_data.get("base_token_address", "")
    quote_addr = pool_data.get("quote_token_address", "")
    base_sym = pool_data.get("base_token_symbol", "Base")[:6]
    quote_sym = pool_data.get("quote_token_symbol", "Quote")[:6]

    # Build keyboard
    dex_id = pool_data.get("dex_id", "")
    supports_liquidity = can_fetch_liquidity(dex_id, network)

    keyboard = []

    # Token info buttons if addresses are available
    if base_addr or quote_addr:
        token_row = []
        if base_addr:
            token_row.append(
                InlineKeyboardButton(
                    text=f"🪙 {base_sym}", callback_data="dex:gecko_token:base"
                )
            )
        if quote_addr:
            token_row.append(
                InlineKeyboardButton(
                    text=f"🪙 {quote_sym}", callback_data="dex:gecko_token:quote"
                )
            )
        if token_row:
            keyboard.append(token_row)

    keyboard.append(
        [
            InlineKeyboardButton(
                text="📈 Chart", callback_data=f"dex:gecko_pool:{pool_index}"
            ),
            InlineKeyboardButton(text="💱 Swap", callback_data="dex:gecko_swap"),
            InlineKeyboardButton(text="📜 Trades", callback_data="dex:gecko_trades"),
        ]
    )

    if supports_liquidity:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="➕ Add Liquidity", callback_data="dex:gecko_add_liquidity"
                ),
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(text="« Back", callback_data="dex:gecko_back_to_list"),
        ]
    )

    # Handle case when returning from photo (OHLCV chart) - can't edit photo to text
    if getattr(query.message, "photo", None):
        await query.message.delete()
        await query.message.answer(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            link_preview_options=NO_LINK_PREVIEW,
        )
    else:
        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            link_preview_options=NO_LINK_PREVIEW,
        )
