"""Pool info lookup by address."""

import logging

from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from config_manager import get_client
from utils.telegram_formatters import (
    escape_markdown_v2,
    format_error_message,
)

from .._shared import (
    resolve_network_id,
)
from ._common import SOLANA_CLMM_NETWORK
from .pool_detail import _show_pool_detail

logger = logging.getLogger(__name__)


async def handle_pool_info(update: Update, context: Context) -> None:
    """Handle CLMM pool info lookup by address"""
    help_text = (
        r"🔍 *Pool Info*" + "\n\n"
        r"Reply with:" + "\n\n"
        r"`connector pool_address [network]`" + "\n\n"
        r"*Solana \(network optional\):*" + "\n"
        r"`meteora 5Q5...abc`" + "\n"
        r"`raydium 7Xy...def`" + "\n"
        r"`orca 3Ab...ghi`" + "\n\n"
        r"*EVM \(network required\):*" + "\n"
        r"`uniswap 0x1...abc ethereum`" + "\n"
        r"`pancakeswap 0x2...def bsc`"
    )

    keyboard = [[InlineKeyboardButton(text="✕ Cancel", callback_data="dex:liquidity")]]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    context.user_data["dex_state"] = "pool_info"

    await update.callback_query.message.answer(
        help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def process_pool_info(update: Update, context: Context, user_input: str) -> None:
    """Process pool info lookup by address - shows full pool details with chart"""
    try:
        parts = user_input.split()
        if len(parts) < 2:
            raise ValueError(
                "Need: connector pool_address [network]\n\nExample: meteora 5Q5...abc"
            )

        connector = parts[0].lower()
        pool_address = parts[1]

        # Validate connector - must be a supported CLMM connector
        solana_connectors = ["meteora", "raydium", "orca"]
        evm_connectors = ["uniswap", "pancakeswap"]
        supported_connectors = solana_connectors + evm_connectors

        if connector not in supported_connectors:
            raise ValueError(
                f"Unsupported connector '{connector}'. Use: {', '.join(supported_connectors)}"
            )

        chat_id = update.effective_chat.id
        client = await get_client(chat_id, user_data=context.user_data)

        if not hasattr(client, "gateway_clmm"):
            raise ValueError("Gateway CLMM not available")

        # Determine network based on connector type
        if connector in solana_connectors:
            network = SOLANA_CLMM_NETWORK
        else:
            # EVM connectors require network specification
            if len(parts) < 3:
                raise ValueError(
                    f"EVM connector '{connector}' requires network.\n\n"
                    f"Example: {connector} {pool_address} ethereum"
                )
            # Gateway owns the network ids; ask it rather than guessing.
            network = await resolve_network_id(client, parts[2])

        # Send loading message
        loading_msg = await update.message.answer(
            "⏳ " + escape_markdown_v2("Loading pool details…"), parse_mode="MarkdownV2"
        )

        # Fetch pool info
        result = await client.gateway_clmm.get_pool_info(
            connector=connector,
            network=network,
            pool_address=pool_address,
        )

        # Delete loading message
        try:
            await loading_msg.delete()
        except Exception:
            pass

        if not result:
            message = escape_markdown_v2(f"❌ Pool not found: {pool_address[:16]}...")
            keyboard = [
                [InlineKeyboardButton(text="« Back", callback_data="dex:liquidity")]
            ]
            reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
            await update.message.answer(
                message, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
            return

        # Construct pool dict for _show_pool_detail. Only the fields hapi's
        # CLMMPoolInfoResponse actually carries: TVL/volume/APR are pool-LIST
        # fields and simply do not exist on pool-info.
        pool = {
            "pool_address": pool_address,
            "address": pool_address,
            "connector": connector,
            "network": network,
            "bin_step": result.get("bin_step"),
            "current_price": result.get("price"),
            "mint_x": result.get("base_token_address"),
            "mint_y": result.get("quote_token_address"),
        }

        # Use the rich pool detail display with chart and add liquidity button
        # has_list_context=False since there's no list to go back to
        await _show_pool_detail(
            update, context, pool, from_callback=False, has_list_context=False
        )

    except Exception as e:
        logger.error(f"Error getting pool info: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to get pool info: {str(e)}")
        await update.message.answer(error_message, parse_mode="MarkdownV2")


def _build_balance_table_compact(gateway_data: dict) -> str:
    """Build a compact balance table for display in pool list prompt (Solana tokens only)"""
    if not gateway_data or not gateway_data.get("balances_by_network"):
        return r"_💡 Use /lp to load your wallet tokens_" + "\n\n"

    # Find Solana balances specifically (Meteora is Solana-based)
    solana_balances = None
    for network, balances in gateway_data["balances_by_network"].items():
        if "solana" in network.lower() and balances:
            solana_balances = balances
            break

    if not solana_balances:
        return r"_💡 No Solana tokens found_" + "\n\n"

    lines = [r"💰 *Your Solana Tokens:*" + "\n"]
    lines.append(f"```")
    lines.append(f"{'Token':<8} {'Amount':<12} {'Value':>8}")
    lines.append(f"{'─'*8} {'─'*12} {'─'*8}")

    # Show top 8 tokens
    for bal in solana_balances[:8]:
        token = bal["token"][:7]
        units = bal["units"]
        value = bal["value"]

        # Format units compactly
        if units >= 1000:
            units_str = f"{units/1000:.1f}K"
        elif units >= 1:
            units_str = f"{units:.2f}"
        else:
            units_str = f"{units:.4f}"
        units_str = units_str[:11]

        # Format value
        if value >= 1000:
            value_str = f"${value/1000:.1f}K"
        else:
            value_str = f"${value:.0f}"
        value_str = value_str[:8]

        lines.append(f"{token:<8} {units_str:<12} {value_str:>8}")

    lines.append(f"```\n")

    return "\n".join(lines)
