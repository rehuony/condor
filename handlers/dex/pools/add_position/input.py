"""Add-position free-text input processors."""

import logging
import re
from decimal import Decimal

from condor.preferences import set_dex_last_pool
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

from .._common import SOLANA_CLMM_NETWORK
from ..add_position.menu import show_add_position_menu
from ..add_position.range_math import _price_to_percent
from ..pool_detail import _show_pool_detail

logger = logging.getLogger(__name__)


def _parse_multi_field_input(user_input: str, current_price: float = None) -> dict:
    """
    Parse multi-field input for position parameters.

    Supports formats like:
    - l:0.8892 - u:1.47
    - l:0.8892 - u:1.47 - b:20% - q:20%
    - L:100 U:200 B:10% Q:10%  (spaces without dashes also work)
    - l:-5% - u:+10%  (percentage relative to current price)
    - l:5% - u:10%  (shorthand: l:X% = -X%, u:X% = +X%)

    Keys (case-insensitive):
    - l, lower = lower_price
    - u, upper = upper_price
    - b, base = amount_base
    - q, quote = amount_quote

    Returns:
        Dict with parsed field updates, or empty dict if not multi-field format
    """
    result = {}

    # Key mappings (case-insensitive)
    key_map = {
        "l": "lower_price",
        "lower": "lower_price",
        "u": "upper_price",
        "upper": "upper_price",
        "b": "amount_base",
        "base": "amount_base",
        "q": "amount_quote",
        "quote": "amount_quote",
    }

    # Check if this looks like multi-field input (contains key:value pattern)
    if ":" not in user_input:
        return {}

    # Use regex to find key:value patterns directly (handles negative numbers correctly)
    # Pattern: word followed by : followed by value (which may start with - or +)
    pattern = r"([a-zA-Z]+)\s*:\s*([+-]?[\d.]+%?|[\d.]+)"
    matches = re.findall(pattern, user_input, re.IGNORECASE)

    # Convert matches to parts format for compatibility with existing logic
    parts = [f"{k}:{v}" for k, v in matches]

    # If no regex matches, fall back to simple split (for backwards compatibility)
    if not parts:
        parts = re.split(r"\s*,\s*|\s+", user_input.strip())

    for part in parts:
        part = part.strip()
        if ":" in part:
            key_val = part.split(":", 1)
            if len(key_val) == 2:
                key = key_val[0].strip().lower()
                value = key_val[1].strip()
                if key in key_map and value:
                    field_name = key_map[key]

                    # Handle percentage for lower_price and upper_price
                    if (
                        field_name in ("lower_price", "upper_price")
                        and value.endswith("%")
                        and current_price
                    ):
                        try:
                            # Parse percentage value (supports +10%, -5%, or just 5%)
                            pct_str = value[:-1].strip()
                            pct = float(pct_str)

                            # Default behavior: lower uses negative, upper uses positive
                            if field_name == "lower_price":
                                # l:5% means -5%, l:-5% means -5%, l:+5% means +5%
                                if not pct_str.startswith(
                                    "+"
                                ) and not pct_str.startswith("-"):
                                    pct = -abs(pct)  # Default to negative for lower
                                # Store the percentage for display
                                result["lower_pct"] = pct
                            else:  # upper_price
                                # u:5% means +5%, u:+5% means +5%, u:-5% means -5%
                                if not pct_str.startswith(
                                    "+"
                                ) and not pct_str.startswith("-"):
                                    pct = abs(pct)  # Default to positive for upper
                                # Store the percentage for display
                                result["upper_pct"] = pct

                            # Calculate price based on percentage
                            calculated_price = current_price * (1 + pct / 100)
                            value = f"{calculated_price:.10f}".rstrip("0").rstrip(".")
                        except (ValueError, TypeError):
                            pass  # Keep original value if parsing fails
                    elif (
                        field_name in ("lower_price", "upper_price")
                        and not value.endswith("%")
                        and current_price
                    ):
                        # Calculate percentage from absolute price
                        try:
                            price_val = float(value)
                            pct = _price_to_percent(current_price, price_val)
                            if field_name == "lower_price":
                                result["lower_pct"] = pct
                            else:
                                result["upper_pct"] = pct
                        except (ValueError, TypeError):
                            pass

                    result[field_name] = value

    return result


async def process_add_position(
    update: Update, context: Context, user_input: str
) -> None:
    """Process add position from text input.

    Supports two input formats:
    1. Multi-field: l:0.8892 - u:1.47 - b:20% - q:20% (updates params and shows menu)
       - Also supports percentage for L/U: l:-5% - u:+10% (relative to current price)
    2. Full input: pool_address lower_price upper_price amount_base amount_quote (executes)
    """
    try:
        # Get current price for percentage calculations
        selected_pool = context.user_data.get("selected_pool", {})
        pool_info = context.user_data.get("selected_pool_info", {})
        current_price = (
            pool_info.get("price")
            or selected_pool.get("current_price")
            or selected_pool.get("price")
        )

        try:
            current_price_float = float(current_price) if current_price else None
        except (ValueError, TypeError):
            current_price_float = None

        # First, check if this is multi-field input (quick updates)
        multi_updates = _parse_multi_field_input(user_input, current_price_float)
        if multi_updates:
            params = context.user_data.get("add_position_params", {})
            params.update(multi_updates)
            context.user_data["add_position_params"] = params

            # Delete user's input message to keep chat clean
            try:
                await update.message.delete()
            except Exception:
                pass

            # Edit existing menu message instead of sending new one
            stored_msg_id = context.user_data.get(
                "add_position_menu_msg_id"
            ) or context.user_data.get("pool_detail_message_id")
            stored_chat_id = context.user_data.get(
                "add_position_menu_chat_id"
            ) or context.user_data.get("pool_detail_chat_id")

            if stored_msg_id and stored_chat_id and selected_pool:
                # Use show_add_position_menu which handles editing properly
                # Create a minimal update-like object to simulate callback
                await show_add_position_menu(update, context, send_new=False)
            elif selected_pool:
                # Fallback: show pool detail (will send new message)
                await _show_pool_detail(
                    update, context, selected_pool, from_callback=False
                )
            return

        # Fall back to original full-input format
        parts = user_input.split()

        if len(parts) < 5:
            raise ValueError(
                "Need: pool_address lower_price upper_price amount_base amount_quote"
            )

        params = context.user_data.get("add_position_params", {})
        params["pool_address"] = parts[0]
        params["lower_price"] = parts[1]
        params["upper_price"] = parts[2]
        params["amount_base"] = parts[3]
        params["amount_quote"] = parts[4]

        # Now execute
        connector = params.get("connector", "meteora")
        network = params.get("network") or SOLANA_CLMM_NETWORK
        strategy_type = int(params.get("strategy_type", "0"))

        chat_id = update.effective_chat.id
        client = await get_client(chat_id, user_data=context.user_data)

        if not hasattr(client, "gateway_clmm"):
            raise ValueError("Gateway CLMM not available")

        # strategyType is Meteora-only; hapi rejects it for other connectors
        extra_params = (
            {"strategyType": strategy_type} if connector == "meteora" else None
        )

        result = await client.gateway_clmm.open_position(
            connector=connector,
            network=network,
            pool_address=params["pool_address"],
            lower_price=Decimal(params["lower_price"]),
            upper_price=Decimal(params["upper_price"]),
            base_token_amount=(
                Decimal(params["amount_base"]) if params["amount_base"] else None
            ),
            quote_token_amount=(
                Decimal(params["amount_quote"]) if params["amount_quote"] else None
            ),
            extra_params=extra_params,
        )

        if result is None:
            raise ValueError("Gateway returned no response.")

        set_dex_last_pool(
            context.user_data,
            {
                "connector": connector,
                "network": network,
                "pool_address": params["pool_address"],
            },
        )

        pos_info = escape_markdown_v2(
            f"✅ Position Added!\n\n"
            f"Pool: {params['pool_address'][:16]}...\n"
            f"Range: [{params['lower_price']} - {params['upper_price']}]\n"
            f"Base: {params['amount_base']}\n"
            f"Quote: {params['amount_quote']}\n"
        )

        if isinstance(result, dict):
            # transaction_hash is None while the open tx is still pending
            if result.get("transaction_hash"):
                pos_info += escape_markdown_v2(
                    f"\nTx: {result['transaction_hash'][:16]}..."
                )
            # position_address is null when the tx was submitted but not yet
            # confirmed — hapi's poller records the position once it lands
            if result.get("position_address"):
                pos_info += escape_markdown_v2(
                    f"\nPosition: {result['position_address'][:16]}..."
                )
            elif result.get("status") == "submitted":
                pos_info += escape_markdown_v2(
                    "\nPosition: pending confirmation (address known once the tx lands)"
                )

        keyboard = [
            [
                InlineKeyboardButton(
                    text="« Back to Liquidity", callback_data="dex:liquidity"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        await update.message.answer(
            pos_info, parse_mode="MarkdownV2", reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error adding position: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to add position: {str(e)}")
        await update.message.answer(error_message, parse_mode="MarkdownV2")


async def process_pos_set_connector(
    update: Update, context: Context, user_input: str
) -> None:
    """Process position set connector input"""
    params = context.user_data.get("add_position_params", {})
    params["connector"] = user_input.strip()
    context.user_data["dex_state"] = "add_position"

    success_msg = escape_markdown_v2(f"✅ Connector set to: {user_input}")
    await update.message.answer(success_msg, parse_mode="MarkdownV2")
    await show_add_position_menu(update, context, send_new=True)


async def process_pos_set_network(
    update: Update, context: Context, user_input: str
) -> None:
    """Process position set network input"""
    params = context.user_data.get("add_position_params", {})
    params["network"] = user_input.strip()
    context.user_data["dex_state"] = "add_position"

    success_msg = escape_markdown_v2(f"✅ Network set to: {user_input}")
    await update.message.answer(success_msg, parse_mode="MarkdownV2")
    await show_add_position_menu(update, context, send_new=True)


async def process_pos_set_pool(
    update: Update, context: Context, user_input: str
) -> None:
    """Process position set pool input"""
    params = context.user_data.get("add_position_params", {})
    params["pool_address"] = user_input.strip()
    context.user_data["dex_state"] = "add_position"

    success_msg = escape_markdown_v2(f"✅ Pool set to: {user_input[:16]}...")
    await update.message.answer(success_msg, parse_mode="MarkdownV2")
    await show_add_position_menu(update, context, send_new=True)


async def process_pos_set_lower(
    update: Update, context: Context, user_input: str
) -> None:
    """Process position set lower price input"""
    params = context.user_data.get("add_position_params", {})
    params["lower_price"] = user_input.strip()
    context.user_data["dex_state"] = "add_position"

    # Refresh pool detail to show updated chart with range lines
    selected_pool = context.user_data.get("selected_pool", {})
    if selected_pool:
        # Show loading message
        loading_msg = await update.message.answer(
            escape_markdown_v2(f"✅ Lower set to: {user_input} - Updating chart..."),
            parse_mode="MarkdownV2",
        )
        timeframe = params.get("timeframe", "1h")
        await _show_pool_detail(
            update, context, selected_pool, from_callback=False, timeframe=timeframe
        )
        # Delete loading message after chart is shown
        try:
            await loading_msg.delete()
        except Exception:
            pass
    else:
        await update.message.answer(
            escape_markdown_v2(f"✅ Lower price set to: {user_input}"),
            parse_mode="MarkdownV2",
        )


async def process_pos_set_upper(
    update: Update, context: Context, user_input: str
) -> None:
    """Process position set upper price input"""
    params = context.user_data.get("add_position_params", {})
    params["upper_price"] = user_input.strip()
    context.user_data["dex_state"] = "add_position"

    # Refresh pool detail to show updated chart with range lines
    selected_pool = context.user_data.get("selected_pool", {})
    if selected_pool:
        # Show loading message
        loading_msg = await update.message.answer(
            escape_markdown_v2(f"✅ Upper set to: {user_input} - Updating chart..."),
            parse_mode="MarkdownV2",
        )
        timeframe = params.get("timeframe", "1h")
        await _show_pool_detail(
            update, context, selected_pool, from_callback=False, timeframe=timeframe
        )
        # Delete loading message after chart is shown
        try:
            await loading_msg.delete()
        except Exception:
            pass
    else:
        await update.message.answer(
            escape_markdown_v2(f"✅ Upper price set to: {user_input}"),
            parse_mode="MarkdownV2",
        )


async def process_pos_set_base(
    update: Update, context: Context, user_input: str
) -> None:
    """Process position set base amount input"""
    params = context.user_data.get("add_position_params", {})
    params["amount_base"] = user_input.strip()
    context.user_data["dex_state"] = "add_position"

    # Refresh pool detail view
    selected_pool = context.user_data.get("selected_pool", {})
    if selected_pool:
        # Show loading message
        loading_msg = await update.message.answer(
            escape_markdown_v2(f"✅ Base set to: {user_input} - Updating chart..."),
            parse_mode="MarkdownV2",
        )
        timeframe = params.get("timeframe", "1h")
        await _show_pool_detail(
            update, context, selected_pool, from_callback=False, timeframe=timeframe
        )
        # Delete loading message after chart is shown
        try:
            await loading_msg.delete()
        except Exception:
            pass
    else:
        await update.message.answer(
            escape_markdown_v2(f"✅ Base amount set to: {user_input}"),
            parse_mode="MarkdownV2",
        )


async def process_pos_set_quote(
    update: Update, context: Context, user_input: str
) -> None:
    """Process position set quote amount input"""
    params = context.user_data.get("add_position_params", {})
    params["amount_quote"] = user_input.strip()
    context.user_data["dex_state"] = "add_position"

    # Refresh pool detail view
    selected_pool = context.user_data.get("selected_pool", {})
    if selected_pool:
        # Show loading message
        loading_msg = await update.message.answer(
            escape_markdown_v2(f"✅ Quote set to: {user_input} - Updating chart..."),
            parse_mode="MarkdownV2",
        )
        timeframe = params.get("timeframe", "1h")
        await _show_pool_detail(
            update, context, selected_pool, from_callback=False, timeframe=timeframe
        )
        # Delete loading message after chart is shown
        try:
            await loading_msg.delete()
        except Exception:
            pass
    else:
        await update.message.answer(
            escape_markdown_v2(f"✅ Quote amount set to: {user_input}"),
            parse_mode="MarkdownV2",
        )
