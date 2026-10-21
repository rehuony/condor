"""Add-position parameter setters and the confirm step."""

import logging
from decimal import Decimal

from condor.preferences import set_dex_last_pool
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
    format_compact_number,
    format_error_message,
)

from ..add_position.range_math import _validate_bin_range

logger = logging.getLogger(__name__)


QUICK_EDIT_PROMPT_KEY = "add_position_prompt_msg_id"


async def handle_pos_quick_edit(update: Update, context: Context) -> None:
    """Arm range/amount edits as typed text; the menu itself never captures it."""
    text = ui.prompt(
        "Quick Edit",
        ui.screen(
            ui.esc("Send one or more fields. Percentages are relative to the price."),
            [
                ui.bullet("L", "lower price or -5%"),
                ui.bullet("U", "upper price or 5%"),
                ui.bullet("B", "base amount or 10%"),
                ui.bullet("Q", "quote amount or 10%"),
            ],
        ),
        "L:-5% U:5% B:10% Q:10%",
        raw=True,
    )
    context.user_data["dex_state"] = "add_position_quick"
    sent = await update.callback_query.message.answer(
        text,
        parse_mode="MarkdownV2",
        reply_markup=ui.keyboard([ui.cancel_button("dex:pos_quick_cancel")]),
    )
    context.user_data[QUICK_EDIT_PROMPT_KEY] = sent.message_id


async def close_quick_edit_prompt(context: Context, chat_id: int) -> None:
    """Remove the Quick Edit prompt once it is answered or cancelled."""
    message_id = context.user_data.pop(QUICK_EDIT_PROMPT_KEY, None)
    if message_id is None:
        return
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.debug(f"Could not delete quick edit prompt: {e}")


async def handle_pos_quick_cancel(update: Update, context: Context) -> None:
    if context.user_data.get("dex_state") == "add_position_quick":
        context.user_data["dex_state"] = "add_position"
    await close_quick_edit_prompt(context, update.effective_chat.id)


async def handle_pos_set_connector(update: Update, context: Context) -> None:
    """Prompt user to input connector for position"""
    help_text = (
        r"📝 *Set Connector*" + "\n\n"
        r"Enter the CLMM connector name:" + "\n\n"
        r"*Examples:*" + "\n"
        r"`meteora` \- Solana CLMM" + "\n"
        r"`raydium` \- Solana CLMM"
    )

    keyboard = [[InlineKeyboardButton(text="« Back", callback_data="dex:add_position")]]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    context.user_data["dex_state"] = "pos_set_connector"

    await update.callback_query.message.answer(
        help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_pos_set_network(update: Update, context: Context) -> None:
    """Prompt user to input network for position"""
    help_text = (
        r"📝 *Set Network*" + "\n\n"
        r"Enter the network name:" + "\n\n"
        r"*Example:*" + "\n"
        r"`solana\-mainnet\-beta`"
    )

    keyboard = [[InlineKeyboardButton(text="« Back", callback_data="dex:add_position")]]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    context.user_data["dex_state"] = "pos_set_network"

    await update.callback_query.message.answer(
        help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_pos_set_pool(update: Update, context: Context) -> None:
    """Prompt user to input pool address"""
    help_text = (
        r"📝 *Set Pool Address*" + "\n\n"
        r"Enter the pool address:" + "\n\n"
        r"*Tip:* Use `/dex_trading` → List Pools to find pool addresses"
    )

    keyboard = [[InlineKeyboardButton(text="« Back", callback_data="dex:add_position")]]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    context.user_data["dex_state"] = "pos_set_pool"

    await update.callback_query.message.answer(
        help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_pos_set_lower(update: Update, context: Context) -> None:
    """Prompt user to input lower price"""
    help_text = (
        r"📝 *Set Lower Price*" + "\n\n"
        r"Enter the lower price bound:" + "\n\n"
        r"*Example:*" + "\n"
        r"`0\.70` \- Lower price bound"
    )

    keyboard = [
        [InlineKeyboardButton(text="« Back", callback_data="dex:pool_detail_refresh")]
    ]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    context.user_data["dex_state"] = "pos_set_lower"

    await update.callback_query.message.answer(
        help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_pos_set_upper(update: Update, context: Context) -> None:
    """Prompt user to input upper price"""
    help_text = (
        r"📝 *Set Upper Price*" + "\n\n"
        r"Enter the upper price bound:" + "\n\n"
        r"*Example:*" + "\n"
        r"`0\.85` \- Upper price bound"
    )

    keyboard = [
        [InlineKeyboardButton(text="« Back", callback_data="dex:pool_detail_refresh")]
    ]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    context.user_data["dex_state"] = "pos_set_upper"

    await update.callback_query.message.answer(
        help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_pos_set_base(update: Update, context: Context) -> None:
    """Prompt user to input base amount"""
    # Get balance for display
    balances = context.user_data.get("token_balances", {})
    base_bal = balances.get("base_balance", 0)
    bal_info = (
        f"_Balance: {escape_markdown_v2(format_compact_number(base_bal))}_\n\n"
        if base_bal > 0
        else ""
    )

    help_text = (
        r"📝 *Set Base Token Amount*"
        + "\n\n"
        + bal_info
        + r"Enter the amount of base token:"
        + "\n\n"
        r"*Examples:*" + "\n"
        r"`10%` \- 10% of your balance" + "\n"
        r"`100` \- Exact 100 tokens" + "\n"
        r"`0\.5` \- Exact 0\.5 tokens"
    )

    keyboard = [
        [InlineKeyboardButton(text="« Back", callback_data="dex:pool_detail_refresh")]
    ]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    context.user_data["dex_state"] = "pos_set_base"

    await update.callback_query.message.answer(
        help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_pos_set_quote(update: Update, context: Context) -> None:
    """Prompt user to input quote amount"""
    # Get balance for display
    balances = context.user_data.get("token_balances", {})
    quote_bal = balances.get("quote_balance", 0)
    bal_info = (
        f"_Balance: {escape_markdown_v2(format_compact_number(quote_bal))}_\n\n"
        if quote_bal > 0
        else ""
    )

    help_text = (
        r"📝 *Set Quote Token Amount*"
        + "\n\n"
        + bal_info
        + r"Enter the amount of quote token:"
        + "\n\n"
        r"*Examples:*" + "\n"
        r"`10%` \- 10% of your balance" + "\n"
        r"`50` \- Exact 50 tokens" + "\n"
        r"`0\.5` \- Exact 0\.5 tokens"
    )

    keyboard = [
        [InlineKeyboardButton(text="« Back", callback_data="dex:pool_detail_refresh")]
    ]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    context.user_data["dex_state"] = "pos_set_quote"

    await update.callback_query.message.answer(
        help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


def _parse_amount(amount_str: str, balance: float) -> Decimal:
    """Parse amount string - supports percentage (10%) or absolute values

    Args:
        amount_str: Amount string like "10%", "100", "0.5"
        balance: Wallet balance for percentage calculation

    Returns:
        Decimal amount
    """
    if not amount_str:
        return None

    amount_str = amount_str.strip()

    # Check if it's a percentage
    if amount_str.endswith("%"):
        try:
            pct = float(amount_str[:-1])
            return Decimal(str(balance * pct / 100))
        except (ValueError, TypeError):
            return None

    # Otherwise it's an absolute amount
    try:
        return Decimal(amount_str)
    except (ValueError, TypeError):
        return None


async def handle_pos_add_confirm(update: Update, context: Context) -> None:
    """Execute adding the position"""
    query = update.callback_query

    try:
        params = context.user_data.get("add_position_params", {})

        connector = params.get("connector")
        network = params.get("network")
        pool_address = params.get("pool_address")
        lower_price = params.get("lower_price")
        upper_price = params.get("upper_price")
        amount_base_str = params.get("amount_base")
        amount_quote_str = params.get("amount_quote")
        strategy_type = int(params.get("strategy_type", "0"))

        # Validate required parameters
        if not all([connector, network, pool_address, lower_price, upper_price]):
            raise ValueError(
                "Missing required parameters (connector, network, pool, prices)"
            )

        if not amount_base_str and not amount_quote_str:
            raise ValueError("Need at least one amount (base or quote)")

        # Validate bin range (max 68 bins to be safe)
        selected_pool = context.user_data.get("selected_pool", {})
        pool_info = context.user_data.get("selected_pool_info", {})
        current_price = (
            pool_info.get("price")
            or selected_pool.get("current_price")
            or selected_pool.get("price")
        )
        bin_step = pool_info.get("bin_step") or selected_pool.get("bin_step")

        if current_price and bin_step:
            try:
                is_valid, total_bins, error_msg = _validate_bin_range(
                    float(lower_price),
                    float(upper_price),
                    float(current_price),
                    int(bin_step),
                    max_bins=68,
                )
                if not is_valid:
                    raise ValueError(error_msg)
            except ValueError:
                raise
            except Exception as e:
                logger.warning(f"Could not validate bin range: {e}")

        # Show loading message immediately
        await query.answer()
        loading_msg = (
            r"⏳ *Adding Liquidity\.\.\.*"
            + "\n\n"
            + escape_markdown_v2(f"Pool: {pool_address[:16]}...")
            + "\n"
            + escape_markdown_v2(f"Range: [{lower_price[:8]} - {upper_price[:8]}]")
            + "\n\n"
            + r"_Please wait, this may take a moment\._"
        )

        # Edit the current message to show loading state
        try:
            if getattr(query.message, "photo", None):
                await query.message.edit_caption(
                    caption=loading_msg, parse_mode="MarkdownV2"
                )
            else:
                await query.message.edit_text(loading_msg, parse_mode="MarkdownV2")
        except Exception:
            pass

        chat_id = update.effective_chat.id
        client = await get_client(chat_id, user_data=context.user_data)

        if not hasattr(client, "gateway_clmm"):
            raise ValueError("Gateway CLMM not available")

        # Get token balances for percentage calculation
        balances = context.user_data.get("token_balances", {})
        base_balance = balances.get("base_balance", 0)
        quote_balance = balances.get("quote_balance", 0)

        # Parse amounts (handles both % and absolute values)
        amount_base = (
            _parse_amount(amount_base_str, base_balance) if amount_base_str else None
        )
        amount_quote = (
            _parse_amount(amount_quote_str, quote_balance) if amount_quote_str else None
        )

        # Validate we have at least one valid amount
        if amount_base is None and amount_quote is None:
            raise ValueError(
                "Invalid amounts. Use '10%' for percentage or '100' for absolute value."
            )

        # Check if we have at least one non-zero amount
        base_is_zero = amount_base is None or amount_base == 0
        quote_is_zero = amount_quote is None or amount_quote == 0
        if base_is_zero and quote_is_zero:
            raise ValueError(
                "Both amounts are 0. Need at least one token to add liquidity."
            )

        # strategyType is Meteora-only; hapi rejects it for other connectors
        extra_params = (
            {"strategyType": strategy_type} if connector == "meteora" else None
        )

        result = await client.gateway_clmm.open_position(
            connector=connector,
            network=network,
            pool_address=pool_address,
            lower_price=Decimal(lower_price),
            upper_price=Decimal(upper_price),
            base_token_amount=amount_base,
            quote_token_amount=amount_quote,
            extra_params=extra_params,
        )

        if result is None:
            raise ValueError("Gateway returned no response.")

        # Clear the positions cache to force fresh fetch
        context.user_data.pop("positions_cache", None)
        context.user_data.pop("all_positions", None)

        # Save pool params
        set_dex_last_pool(
            context.user_data,
            {"connector": connector, "network": network, "pool_address": pool_address},
        )

        # Strategy name for display
        strategy_names = {0: "Spot", 1: "Curve", 2: "BidAsk"}
        strategy_name = strategy_names.get(strategy_type, "Spot")

        pos_info = escape_markdown_v2(
            f"✅ Position Added!\n\n"
            f"Connector: {connector}\n"
            f"Pool: {pool_address[:16]}...\n"
            f"Range: [{lower_price[:8]} - {upper_price[:8]}]\n"
            f"Strategy: {strategy_name}\n"
        )

        if amount_base:
            pos_info += escape_markdown_v2(f"Base: {float(amount_base):.6f}\n")
        if amount_quote:
            pos_info += escape_markdown_v2(f"Quote: {float(amount_quote):.6f}\n")

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

        # Edit the loading message with success result
        try:
            if getattr(query.message, "photo", None):
                await query.message.edit_caption(
                    caption=pos_info, parse_mode="MarkdownV2", reply_markup=reply_markup
                )
            else:
                await query.message.edit_text(
                    pos_info, parse_mode="MarkdownV2", reply_markup=reply_markup
                )
        except Exception:
            await query.message.answer(
                pos_info, parse_mode="MarkdownV2", reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Error adding position: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to add position: {str(e)}")
        keyboard = [
            [
                InlineKeyboardButton(
                    text="« Back", callback_data="dex:pool_detail_refresh"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        try:
            if getattr(query.message, "photo", None):
                await query.message.edit_caption(
                    caption=error_message,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup,
                )
            else:
                await query.message.edit_text(
                    error_message, parse_mode="MarkdownV2", reply_markup=reply_markup
                )
        except Exception:
            await query.message.answer(
                error_message, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
