"""Add a pool's tokens to Gateway and restart it."""

import logging

from condor.pool_data import (
    gecko_call,
    gecko_request,
)
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from utils.telegram_formatters import escape_markdown_v2

from ._format import _parse_token_address_from_id

logger = logging.getLogger(__name__)


async def handle_gecko_add_tokens(update: Update, context: Context) -> None:
    """Add pool tokens to Gateway configuration"""
    from config_manager import get_config_manager

    query = update.callback_query

    pool_data = context.user_data.get("gecko_selected_pool")
    if not pool_data:
        await query.answer("No pool selected")
        return

    # Get token addresses
    base_addr = pool_data.get("base_token_address", "")
    quote_addr = pool_data.get("quote_token_address", "")
    gecko_network = pool_data.get("network", "solana")
    pool_address = pool_data.get("address", "")

    # If no addresses in pool_data, fetch from GeckoTerminal API directly
    if not base_addr and not quote_addr and pool_address:
        await query.answer("Fetching token info...")
        try:
            # A raw path because geckoterminal_py exposes no get_pool, but still
            # through gecko_request so it draws on the same shared budget.
            result = await gecko_request(
                "GET",
                f"networks/{gecko_network}/pools/{pool_address}",
                params={"include": "base_token,quote_token"},
            )
            data = result.get("data", {})
            relationships = data.get("relationships", {})
            base_token_id = (
                relationships.get("base_token", {}).get("data", {}).get("id", "")
            )
            quote_token_id = (
                relationships.get("quote_token", {}).get("data", {}).get("id", "")
            )
            base_addr = _parse_token_address_from_id(base_token_id)
            quote_addr = _parse_token_address_from_id(quote_token_id)
        except Exception as e:
            logger.warning(f"Failed to fetch pool info for tokens: {e}")

    if not base_addr and not quote_addr:
        await query.answer("No token addresses available", show_alert=True)
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
    gateway_network = network_mapping.get(gecko_network, gecko_network)

    await query.answer("Adding tokens to Gateway...")

    chat = query.message.chat

    # Show loading - delete current message and send loading text
    try:
        await query.message.delete()
    except Exception:
        pass

    loading_msg = await chat.send_message(
        r"🔄 *Adding tokens to Gateway\.\.\.*", parse_mode="MarkdownV2"
    )

    added_tokens = []
    errors = []

    async def add_token_to_gateway(token_address: str) -> str:
        """Fetch token info and add to gateway. Returns symbol or error indicator."""
        try:
            # Fetch from GeckoTerminal
            result = await gecko_call(
                "get_specific_token_on_network", gecko_network, token_address
            )

            token_data = {}
            if isinstance(result, dict):
                data = result.get("data", result) if "data" in result else result
                token_data = (
                    data.get("attributes", data) if isinstance(data, dict) else {}
                )
            else:
                # Try pandas DataFrame
                try:
                    import pandas as pd

                    if isinstance(result, pd.DataFrame) and not result.empty:
                        token_data = result.to_dict("records")[0]
                except ImportError:
                    pass

            if not token_data:
                return None

            symbol = token_data.get("symbol", "???")
            decimals = token_data.get(
                "decimals", 9 if gecko_network == "solana" else 18
            )
            name = token_data.get("name")

            # Add to gateway
            client = await get_config_manager().get_client()
            await client.gateway.add_token(
                network_id=gateway_network,
                address=token_address,
                symbol=symbol,
                decimals=decimals,
                name=name,
            )
            return symbol

        except Exception as e:
            error_str = str(e).lower()
            if "already exists" in error_str or "duplicate" in error_str:
                return "exists"
            logger.warning(f"Failed to add token {token_address[:12]}...: {e}")
            return None

    # Add both tokens
    if base_addr:
        result = await add_token_to_gateway(base_addr)
        if result and result != "exists":
            added_tokens.append(result)
        elif result is None:
            errors.append(f"base ({base_addr[:8]}...)")

    if quote_addr:
        result = await add_token_to_gateway(quote_addr)
        if result and result != "exists":
            added_tokens.append(result)
        elif result is None:
            errors.append(f"quote ({quote_addr[:8]}...)")

    # Build result message
    if added_tokens:
        result_msg = f"✅ *Added:* {escape_markdown_v2(', '.join(added_tokens))}\n\n"
    else:
        result_msg = "ℹ️ _Tokens already in Gateway_\n\n"

    if errors:
        result_msg += f"⚠️ Failed: {escape_markdown_v2(', '.join(errors))}\n\n"

    result_msg += r"⚠️ _Restart Gateway for changes to take effect_"

    # Add restart button
    keyboard = [
        [
            InlineKeyboardButton(
                text="🔄 Restart Gateway", callback_data="dex:gecko_restart_gateway"
            )
        ],
        [
            InlineKeyboardButton(
                text="« Back to Pool",
                callback_data=f"dex:gecko_pool:{context.user_data.get('gecko_selected_pool_index', 0)}",
            )
        ],
    ]

    try:
        await loading_msg.edit_text(
            result_msg,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
    except Exception:
        await chat.send_message(
            result_msg,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )


async def handle_gecko_restart_gateway(update: Update, context: Context) -> None:
    """Restart Gateway after adding tokens"""
    from config_manager import get_config_manager

    query = update.callback_query
    await query.answer("Restarting Gateway...")

    chat = query.message.chat

    try:
        await query.message.delete()
    except Exception:
        pass

    loading_msg = await chat.send_message(
        r"🔄 *Restarting Gateway\.\.\.*", parse_mode="MarkdownV2"
    )

    try:
        client = await get_config_manager().get_client()
        await client.gateway.restart()

        # Wait a moment for restart
        import asyncio

        await asyncio.sleep(2)

        await loading_msg.edit_text(
            r"✅ *Gateway restarted*" + "\n\n_New tokens are now available_",
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

    except Exception as e:
        logger.error(f"Failed to restart gateway: {e}", exc_info=True)
        await loading_msg.edit_text(
            f"❌ Failed to restart Gateway: {escape_markdown_v2(str(e))}",
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
