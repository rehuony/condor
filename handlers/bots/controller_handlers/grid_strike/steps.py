"""Grid Strike wizard steps: connector, pair, side, leverage, amount, prices, take profit."""

import asyncio
import logging

from condor.telegram.transport import as_input_file
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from handlers.cex._shared import (
    get_cex_balances,
    get_trading_rules,
)
from utils.telegram_formatters import escape_markdown_v2, format_error_message

from ..._shared import (
    SIDE_LONG,
    SIDE_SHORT,
    calculate_auto_prices,
    clear_bots_state,
    fetch_candles,
    fetch_current_price,
    generate_candles_chart,
    generate_config_id,
    get_bots_client,
    get_controller_config,
    init_new_controller_config,
    set_controller_config,
)
from ...controllers._analysis import calculate_natr
from ...controllers.grid_strike.grid_analysis import (
    generate_theoretical_grid,
    suggest_grid_params,
)
from .._wizard_common import (
    WizardSpec,
    show_connector_step,
    show_pair_step,
    show_pair_suggestions,
)

GS_WIZARD = WizardSpec(
    key="gs",
    title=r"Grid Strike",
    log_name="grid strike",
    connector_action="bots:gs_connector:",
    pair_action="bots:gs_pair:",
    pair_select_action="bots:gs_pair_select:",
    back_to_connector="bots:gs_back_to_connector",
)

logger = logging.getLogger(__name__)


async def show_new_grid_strike_form(update: Update, context: Context) -> None:
    """Start the progressive Grid Strike wizard - Step 1: Connector"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    # Clear any cached market data from previous wizard runs
    # This prevents showing stale data when starting a new grid for a different pair
    gs_keys_to_clear = [
        "gs_current_price",
        "gs_candles",
        "gs_candles_interval",
        "gs_chart_interval",
        "gs_natr",
        "gs_trading_rules",
        "gs_theoretical_grid",
        "gs_market_data_ready",
        "gs_market_data_error",
    ]
    for key in gs_keys_to_clear:
        context.user_data.pop(key, None)

    # Fetch existing configs for sequence numbering
    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.list_controller_configs()
        context.user_data["controller_configs_list"] = configs
    except Exception as e:
        logger.warning(f"Could not fetch existing configs for sequencing: {e}")

    # Initialize new config with defaults
    config = init_new_controller_config(context, "grid_strike")
    context.user_data["bots_state"] = "gs_wizard"
    context.user_data["gs_wizard_step"] = "connector_name"
    context.user_data["gs_wizard_message_id"] = query.message.message_id
    context.user_data["gs_wizard_chat_id"] = query.message.chat.id

    # Show connector selector directly
    await _show_wizard_connector_step(update, context)


async def _show_wizard_connector_step(update: Update, context: Context) -> None:
    """Wizard Step 1: Select Connector"""
    await show_connector_step(
        update,
        context,
        GS_WIZARD,
        r"*📈 Grid Strike*" + "\n\n"
        r"A market making strategy that places a series of buy and sell "
        r"limit orders at predetermined price intervals above and below "
        r"a reference price, forming a \"grid\.\"" + "\n\n"
        r"[📖 Read the strategy guide](https://hummingbot.org/blog/strategy-guide-grid-strike/)"
        + "\n\n"
        "\n"
        r"*Step 1: Select Exchange*" + "\n\n"
        r"Grid Strike works on both spot and perpetual exchanges\.",
    )


async def handle_gs_wizard_connector(
    update: Update, context: Context, connector: str
) -> None:
    """Handle connector selection in wizard"""
    query = update.callback_query
    config = get_controller_config(context)

    config["connector_name"] = connector
    set_controller_config(context, config)

    # Move to trading pair step
    context.user_data["gs_wizard_step"] = "trading_pair"
    await _show_wizard_pair_step(update, context)


async def handle_gs_wizard_pair(update: Update, context: Context, pair: str) -> None:
    """Handle trading pair selection from button in wizard"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    # Clear old market data if pair changed (prevents stale data)
    old_pair = config.get("trading_pair", "")
    if old_pair and old_pair.upper() != pair.upper():
        for key in [
            "gs_current_price",
            "gs_candles",
            "gs_candles_interval",
            "gs_natr",
            "gs_trading_rules",
            "gs_theoretical_grid",
            "gs_market_data_ready",
            "gs_market_data_error",
        ]:
            context.user_data.pop(key, None)

    config["trading_pair"] = pair.upper()
    set_controller_config(context, config)

    # Start background fetch of market data
    asyncio.create_task(_background_fetch_market_data(context, config, chat_id))

    # Move to side step
    context.user_data["gs_wizard_step"] = "side"
    await _show_wizard_side_step(update, context)


async def _show_wizard_pair_step(update: Update, context: Context) -> None:
    """Wizard Step 2: Enter Trading Pair"""

    def prompt(connector: str, recent_pairs: list[str]) -> str:
        recent_hint = ""
        if recent_pairs:
            recent_hint = "\n\nOr type a custom pair below:"

        # Determine total steps based on connector type
        is_perp = connector.endswith("_perpetual")
        total_steps = 6 if is_perp else 5

        return (
            rf"*📈 Grid Strike \- Step 2/{total_steps}*" + "\n\n"
            f"🏦 `{escape_markdown_v2(connector)}`" + "\n\n"
            r"🔗 *Trading Pair*" + "\n\n"
            r"Select a recent pair or enter a new one:"
            + escape_markdown_v2(recent_hint)
        )

    await show_pair_step(update, context, GS_WIZARD, prompt)


async def _show_wizard_side_step(update: Update, context: Context) -> None:
    """Wizard Step 3: Select Side"""
    query = update.callback_query
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")

    keyboard = [
        [
            InlineKeyboardButton(text="📈 LONG", callback_data="bots:gs_side:long"),
            InlineKeyboardButton(text="📉 SHORT", callback_data="bots:gs_side:short"),
        ],
        [
            InlineKeyboardButton(text="« Back", callback_data="bots:gs_back_to_pair"),
            InlineKeyboardButton(text="✕ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    # Determine total steps based on connector type
    is_perp = connector.endswith("_perpetual")
    total_steps = 6 if is_perp else 5

    await query.message.edit_text(
        rf"*📈 Grid Strike \- Step 3/{total_steps}*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n\n"
        r"🎯 *Select Side*" + "\n\n"
        r"📈 *Long Grid* \- Opens long positions on dips and closes them "
        r"on bounces\. Bullish bias—profits from oscillations while "
        r"expecting price to trend upward\." + "\n\n"
        r"📉 *Short Grid* \- Opens short positions on rallies and closes "
        r"them on pullbacks\. Bearish bias—profits from oscillations while "
        r"expecting price to trend downward\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_gs_wizard_side(
    update: Update, context: Context, side_str: str
) -> None:
    """Handle side selection in wizard"""
    query = update.callback_query
    config = get_controller_config(context)

    config["side"] = SIDE_LONG if side_str == "long" else SIDE_SHORT
    set_controller_config(context, config)

    connector = config.get("connector_name", "")

    # Only ask for leverage on perpetual exchanges
    if connector.endswith("_perpetual"):
        context.user_data["gs_wizard_step"] = "leverage"
        await _show_wizard_leverage_step(update, context)
    else:
        # Spot exchange - set leverage to 1 and skip to amount
        config["leverage"] = 1
        set_controller_config(context, config)
        context.user_data["gs_wizard_step"] = "total_amount_quote"
        await _show_wizard_amount_step(update, context)


async def _show_wizard_leverage_step(update: Update, context: Context) -> None:
    """Wizard Step 4: Select Leverage"""
    query = update.callback_query
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = "📈 LONG" if config.get("side") == SIDE_LONG else "📉 SHORT"

    # Enable text input for leverage
    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "leverage"

    keyboard = [
        [
            InlineKeyboardButton(text="1x", callback_data="bots:gs_leverage:1"),
            InlineKeyboardButton(text="5x", callback_data="bots:gs_leverage:5"),
            InlineKeyboardButton(text="10x", callback_data="bots:gs_leverage:10"),
        ],
        [
            InlineKeyboardButton(text="20x", callback_data="bots:gs_leverage:20"),
            InlineKeyboardButton(text="50x", callback_data="bots:gs_leverage:50"),
            InlineKeyboardButton(text="75x", callback_data="bots:gs_leverage:75"),
        ],
        [
            InlineKeyboardButton(text="« Back", callback_data="bots:gs_back_to_side"),
            InlineKeyboardButton(text="✕ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    # Leverage step is only shown for perps (always 6 steps)
    await query.message.edit_text(
        r"*📈 Grid Strike \- Step 4/6*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}` \\| {side}"
        + "\n\n"
        r"⚡ *Select Leverage*" + "\n"
        r"_Or type a value \(e\.g\. 2, 3x\)_",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_gs_wizard_leverage(
    update: Update, context: Context, leverage: int
) -> None:
    """Handle leverage selection in wizard"""
    query = update.callback_query
    config = get_controller_config(context)

    config["leverage"] = leverage
    set_controller_config(context, config)

    # Move to amount step
    context.user_data["gs_wizard_step"] = "total_amount_quote"
    await _show_wizard_amount_step(update, context)


async def _show_wizard_amount_step(update: Update, context: Context) -> None:
    """Wizard Step 5: Enter Amount with available balances"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = "📈 LONG" if config.get("side") == SIDE_LONG else "📉 SHORT"
    leverage = config.get("leverage", 1)

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "total_amount_quote"

    # Extract base and quote tokens from pair
    base_token, quote_token = "", ""
    if "-" in pair:
        base_token, quote_token = pair.split("-", 1)

    # Fetch balances for the connector
    balance_text = ""
    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        balances = await get_cex_balances(
            context.user_data, client, "master_account", ttl=30
        )

        # Try to find connector balances with flexible matching
        # (binance_perpetual should match binance_perpetual, binance, etc.)
        connector_balances = []
        connector_lower = connector.lower()
        connector_base = connector_lower.replace("_perpetual", "").replace("_spot", "")

        for bal_connector, bal_list in balances.items():
            bal_lower = bal_connector.lower()
            bal_base = bal_lower.replace("_perpetual", "").replace("_spot", "")
            # Match exact, base name, or if one contains the other
            if bal_lower == connector_lower or bal_base == connector_base:
                connector_balances = bal_list
                logger.debug(
                    f"Found balances for {connector} under key {bal_connector}"
                )
                break

        if connector_balances:
            relevant_balances = []
            for bal in connector_balances:
                token = bal.get("token", bal.get("asset", ""))
                # Portfolio API returns 'units' for available balance
                available = bal.get(
                    "units", bal.get("available_balance", bal.get("free", 0))
                )
                value_usd = bal.get("value", 0)  # USD value if available
                if token and available:
                    try:
                        available_float = float(available)
                        if available_float > 0:
                            # Show quote token and base token balances
                            if token.upper() in [
                                quote_token.upper(),
                                base_token.upper(),
                            ]:
                                relevant_balances.append(
                                    (
                                        token,
                                        available_float,
                                        float(value_usd) if value_usd else None,
                                    )
                                )
                    except (ValueError, TypeError):
                        continue

            if relevant_balances:
                bal_lines = []
                for token, available, value_usd in relevant_balances:
                    # Format amount based on size
                    if available >= 1000:
                        amt_str = f"{available:,.0f}"
                    elif available >= 1:
                        amt_str = f"{available:,.2f}"
                    else:
                        amt_str = f"{available:,.6f}"

                    # Add USD value if available
                    if value_usd and value_usd >= 1:
                        bal_lines.append(f"{token}: {amt_str} (${value_usd:,.0f})")
                    else:
                        bal_lines.append(f"{token}: {amt_str}")
                balance_text = (
                    "💼 *Available:* "
                    + " \\| ".join(escape_markdown_v2(b) for b in bal_lines)
                    + "\n\n"
                )
            else:
                # Connector has balances but not the specific tokens for this pair
                logger.debug(
                    f"Connector {connector} has balances but not {base_token} or {quote_token}"
                )
                balance_text = f"_No {escape_markdown_v2(quote_token)} balance on {escape_markdown_v2(connector)}_\n\n"
        elif balances:
            # Balances exist but not for this connector/pair
            logger.debug(
                f"No balances found for connector {connector} with tokens {base_token}/{quote_token}. Available connectors: {list(balances.keys())}"
            )
            balance_text = f"_No {escape_markdown_v2(quote_token)} balance found_\n\n"
        else:
            logger.debug(f"No balances returned from API for connector {connector}")
    except Exception as e:
        logger.warning(f"Could not fetch balances for amount step: {e}", exc_info=True)

    keyboard = [
        [
            InlineKeyboardButton(text="💵 100", callback_data="bots:gs_amount:100"),
            InlineKeyboardButton(text="💵 500", callback_data="bots:gs_amount:500"),
            InlineKeyboardButton(text="💵 1000", callback_data="bots:gs_amount:1000"),
        ],
        [
            InlineKeyboardButton(text="💰 2000", callback_data="bots:gs_amount:2000"),
            InlineKeyboardButton(text="💰 5000", callback_data="bots:gs_amount:5000"),
        ],
        [
            InlineKeyboardButton(
                text="« Back", callback_data="bots:gs_back_to_leverage"
            ),
            InlineKeyboardButton(text="✕ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    # Determine step number based on connector type
    # Perps: Step 5/6 (has leverage step), Spot: Step 4/5 (no leverage step)
    is_perp = connector.endswith("_perpetual")
    step_num = 5 if is_perp else 4
    total_steps = 6 if is_perp else 5

    # Calculate margin example for leverage explanation
    margin_example = 100 / leverage if leverage > 1 else 100

    message_text = (
        rf"*📈 Grid Strike \- Step {step_num}/{total_steps}*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n"
        f"🎯 {side} \\| ⚡ `{leverage}x`"
        + "\n\n"
        + balance_text
        + r"💰 *Total Amount \(Quote\)*"
        + "\n\n"
        rf"This is the total position size in {escape_markdown_v2(quote_token or 'quote asset')}, "
        r"including leverage\." + "\n\n"
        rf"_Example: 100 {escape_markdown_v2(quote_token or 'USDT')} at {leverage}x \= "
        rf"{escape_markdown_v2(f'{margin_example:.1f}')} margin used_" + "\n\n"
        r"Select a preset or type a custom amount:"
    )

    # Handle both text and photo messages (when going back from chart step)
    try:
        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
    except Exception:
        # Message is likely a photo - delete it and send new text message
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )


async def handle_gs_wizard_amount(
    update: Update, context: Context, amount: float
) -> None:
    """Handle amount selection in wizard"""
    query = update.callback_query
    config = get_controller_config(context)

    config["total_amount_quote"] = amount
    set_controller_config(context, config)

    pair = config.get("trading_pair", "")

    # Always show loading indicator immediately since chart generation takes time
    await query.message.edit_text(
        r"*📈 Grid Strike \- New Config*" + "\n\n"
        f"⏳ *Loading chart for* `{escape_markdown_v2(pair)}`\\.\\.\\." + "\n\n"
        r"_Fetching market data and generating chart\.\.\._",
        parse_mode="MarkdownV2",
    )

    # Move to prices step - this will fetch OHLC and show chart
    context.user_data["gs_wizard_step"] = "prices"
    await _show_wizard_prices_step(update, context)


def _calculate_min_order_amount(
    current_price: float, trading_rules: dict, default: float = 6.0
) -> float:
    """
    Calculate minimum order amount based on trading rules.

    The minimum is the greater of:
    - min_notional_size from trading rules
    - current_price * min_order_size (min base amount)
    - the provided default

    Returns the calculated minimum order amount in quote currency.
    """
    min_notional = trading_rules.get("min_notional_size", 0) or 0
    min_order_size = trading_rules.get("min_order_size", 0) or 0

    # Calculate min from base amount requirement
    min_from_base = current_price * min_order_size if min_order_size > 0 else 0

    # Take the maximum of all constraints
    calculated_min = max(default, min_notional, min_from_base)

    return calculated_min


async def _show_wizard_prices_step(
    update: Update, context: Context, interval: str = None
) -> None:
    """Wizard Step 6: Grid Configuration with prices, TP, spread, and grid analysis"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = config.get("side", SIDE_LONG)
    total_amount = config.get("total_amount_quote", 1000)

    # Get current interval (default 5m for better NATR calculation)
    if interval is None:
        interval = context.user_data.get("gs_chart_interval", "5m")
    context.user_data["gs_chart_interval"] = interval

    # Check if we have pre-cached data from background fetch
    current_price = context.user_data.get("gs_current_price")
    candles = context.user_data.get("gs_candles")

    try:
        # If no cached data or interval changed, fetch now
        cached_interval = context.user_data.get("gs_candles_interval", "5m")
        need_refetch = interval != cached_interval

        if not current_price or need_refetch:
            # Show loading message - handle both text and photo messages
            try:
                await query.message.edit_text(
                    r"*📈 Grid Strike \- New Config*" + "\n\n"
                    f"⏳ Fetching market data for `{escape_markdown_v2(pair)}`\\.\\.\\.",
                    parse_mode="MarkdownV2",
                )
            except Exception:
                # Message is likely a photo - delete it and send new text message
                try:
                    await query.message.delete()
                except Exception:
                    pass
                loading_msg = await context.bot.send_message(
                    chat_id=query.message.chat.id,
                    text=(
                        r"*📈 Grid Strike \- New Config*" + "\n\n"
                        f"⏳ Fetching market data for `{escape_markdown_v2(pair)}`\\.\\.\\."
                    ),
                    parse_mode="MarkdownV2",
                )
                context.user_data["gs_wizard_message_id"] = loading_msg.message_id

            client, _ = await get_bots_client(chat_id, context.user_data)
            current_price = await fetch_current_price(client, connector, pair)

            if current_price:
                context.user_data["gs_current_price"] = current_price
                # Fetch candles for NATR calculation and chart visualization
                candles = await fetch_candles(
                    client, connector, pair, interval=interval, max_records=420
                )
                context.user_data["gs_candles"] = candles
                context.user_data["gs_candles_interval"] = interval

                # Fetch trading rules for validation
                try:
                    rules = await get_trading_rules(
                        context.user_data, client, connector
                    )
                    context.user_data["gs_trading_rules"] = rules.get(pair, {})
                except Exception as e:
                    logger.warning(f"Could not fetch trading rules: {e}")
                    context.user_data["gs_trading_rules"] = {}

        if not current_price:
            keyboard = [
                [InlineKeyboardButton(text="« Back", callback_data="bots:main_menu")]
            ]
            try:
                await query.message.edit_text(
                    r"*❌ Error*" + "\n\n"
                    f"Could not fetch price for `{escape_markdown_v2(pair)}`\\.\n"
                    r"Please check the trading pair and try again\.",
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=query.message.chat.id,
                    text=(
                        r"*❌ Error*" + "\n\n"
                        f"Could not fetch price for `{escape_markdown_v2(pair)}`\\.\n"
                        r"Please check the trading pair and try again\."
                    ),
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                )
            return

        # Calculate NATR from candles
        natr = None
        candles_list = candles.get("data", []) if isinstance(candles, dict) else candles
        logger.info(
            f"Candles for {pair} ({interval}): {len(candles_list) if candles_list else 0} records"
        )
        if candles_list:
            natr = calculate_natr(candles_list, period=14)
            context.user_data["gs_natr"] = natr
            # Use the last candle's close price for better chart alignment
            last_candle = candles_list[-1] if candles_list else None
            if last_candle:
                last_close = last_candle.get("close") or last_candle.get("c")
                if last_close:
                    current_price = float(last_close)
                    context.user_data["gs_current_price"] = current_price

        # Get trading rules
        trading_rules = context.user_data.get("gs_trading_rules", {})
        min_notional = trading_rules.get("min_notional_size", 5.0)
        min_order_size = trading_rules.get("min_order_size", 0)

        # Calculate smart defaults based on NATR if not already set
        if not config.get("start_price") or not config.get("end_price"):
            if natr and natr > 0:
                # Use NATR-based suggestions
                suggestions = suggest_grid_params(
                    current_price, natr, side, total_amount, min_notional
                )
                config["start_price"] = suggestions["start_price"]
                config["end_price"] = suggestions["end_price"]
                config["limit_price"] = suggestions["limit_price"]
                # Note: min_spread_between_orders and take_profit use fixed defaults from config.py
                # NATR-based suggestions are not applied - user prefers consistent defaults
            else:
                # Fallback to default percentages
                start, end, limit = calculate_auto_prices(current_price, side)
                config["start_price"] = start
                config["end_price"] = end
                config["limit_price"] = limit

        start = config.get("start_price")
        end = config.get("end_price")
        limit = config.get("limit_price")
        min_spread = config.get("min_spread_between_orders", 0.0001)
        take_profit = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)

        # Calculate minimum order amount from trading rules
        required_min_order = _calculate_min_order_amount(
            current_price, trading_rules, default=6.0
        )
        min_order_amount = config.get("min_order_amount_quote", required_min_order)

        # Ensure min_order_amount respects exchange rules
        if min_order_amount < required_min_order:
            config["min_order_amount_quote"] = required_min_order
            min_order_amount = required_min_order

        # Generate config ID with sequence number (if not already set)
        if not config.get("id"):
            existing_configs = context.user_data.get("controller_configs_list", [])
            config["id"] = generate_config_id(
                connector, pair, existing_configs=existing_configs
            )

        set_controller_config(context, config)

        # Generate theoretical grid
        grid = generate_theoretical_grid(
            start_price=start,
            end_price=end,
            min_spread=min_spread,
            total_amount=total_amount,
            min_order_amount=min_order_amount,
            current_price=current_price,
            side=side,
            trading_rules=trading_rules,
        )
        context.user_data["gs_theoretical_grid"] = grid

        # Show price edit options
        side_str = "📈 LONG" if side == SIDE_LONG else "📉 SHORT"

        context.user_data["bots_state"] = "gs_wizard_input"
        context.user_data["gs_wizard_step"] = "prices"

        # Build interval buttons with current one highlighted
        interval_options = ["1m", "5m", "15m", "1h", "4h"]
        interval_row = []
        for opt in interval_options:
            label = f"✓ {opt}" if opt == interval else opt
            interval_row.append(
                InlineKeyboardButton(
                    text=label, callback_data=f"bots:gs_interval:{opt}"
                )
            )

        keyboard = [
            interval_row,
            [
                InlineKeyboardButton(
                    text="💾 Save Config", callback_data="bots:gs_save"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="« Back", callback_data="bots:gs_back_to_amount"
                ),
                InlineKeyboardButton(text="✕ Cancel", callback_data="bots:main_menu"),
            ],
        ]

        # Get config values
        max_open_orders = config.get("max_open_orders", 3)
        order_frequency = config.get("order_frequency", 3)
        leverage = config.get("leverage", 1)
        position_mode = config.get("position_mode", "ONEWAY")
        coerce_tp_to_step = config.get("coerce_tp_to_step", False)
        activation_bounds = config.get("activation_bounds", 0.01)
        side_value = config.get("side", SIDE_LONG)
        side_str_label = "LONG" if side_value == SIDE_LONG else "SHORT"

        # Grid analysis info
        grid_valid = "✓" if grid.get("valid") else "⚠️"
        natr_pct = f"{natr*100:.2f}%" if natr else "N/A"
        range_pct = f"{grid.get('grid_range_pct', 0):.2f}%"

        # Determine final step number based on connector type
        is_perp = connector.endswith("_perpetual")
        final_step = 6 if is_perp else 5

        # Build config text with individually copyable key=value params
        config_text = (
            rf"*📈 Grid Strike \- Step {final_step}/{final_step} \(Final\)*" + "\n\n"
            f"*{escape_markdown_v2(pair)}* {side_str_label}\n"
            f"Price: `{current_price:,.6g}` \\| Range: `{range_pct}` \\| NATR: `{natr_pct}`\n\n"
            f"`connector_name={connector}`\n"
            f"`trading_pair={pair}`\n"
            f"`total_amount_quote={total_amount:.0f}`\n"
            f"`start_price={start:.6g}`\n"
            f"`end_price={end:.6g}`\n"
            f"`limit_price={limit:.6g}`\n"
            f"`leverage={leverage}`\n"
            f"`position_mode={position_mode}`\n"
            f"`take_profit={take_profit}`\n"
            f"`coerce_tp_to_step={str(coerce_tp_to_step).lower()}`\n"
            f"`min_spread_between_orders={min_spread}`\n"
            f"`min_order_amount_quote={min_order_amount:.0f}`\n"
            f"`max_open_orders={max_open_orders}`\n"
            f"`activation_bounds={activation_bounds}`\n\n"
            f"{grid_valid} Grid: `{grid['num_levels']}` levels "
            f"\\(↓{grid.get('levels_below_current', 0)} ↑{grid.get('levels_above_current', 0)}\\) "
            f"@ `${grid['amount_per_level']:.2f}`/lvl \\| step: `{grid.get('spread_pct', 0):.3f}%`"
        )

        # Add warnings if any
        if grid.get("warnings"):
            warnings_text = "\n".join(
                f"⚠️ {escape_markdown_v2(w)}" for w in grid["warnings"]
            )
            config_text += f"\n{warnings_text}"

        config_text += "\n\n_Edit: `field=value`_"

        # Generate chart and send as photo with caption
        if candles_list:
            chart_bytes = generate_candles_chart(
                candles_list,
                pair,
                start_price=start,
                end_price=end,
                limit_price=limit,
                current_price=current_price,
                side=side,
            )

            # Delete old message and send photo with caption + buttons
            try:
                await query.message.delete()
            except:
                pass

            msg = await context.bot.send_photo(
                chat_id=query.message.chat.id,
                photo=as_input_file(chart_bytes),
                caption=config_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )

            context.user_data["gs_wizard_message_id"] = msg.message_id
            context.user_data["gs_wizard_chat_id"] = query.message.chat.id
        else:
            # No chart - handle photo messages
            if getattr(query.message, "photo", None):
                try:
                    await query.message.delete()
                except Exception:
                    pass
                msg = await context.bot.send_message(
                    chat_id=query.message.chat.id,
                    text=config_text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                )
                context.user_data["gs_wizard_message_id"] = msg.message_id
            else:
                await query.message.edit_text(
                    text=config_text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                )
                context.user_data["gs_wizard_message_id"] = query.message.message_id

    except Exception as e:
        logger.error(f"Error in prices step: {e}", exc_info=True)
        keyboard = [
            [InlineKeyboardButton(text="« Back", callback_data="bots:main_menu")]
        ]
        error_msg = format_error_message(f"Error fetching market data: {str(e)}")
        try:
            if getattr(query.message, "photo", None):
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await query.message.answer(
                    error_msg,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                )
            else:
                await query.message.edit_text(
                    error_msg,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                )
        except Exception:
            pass


async def handle_gs_back_to_prices(update: Update, context: Context) -> None:
    """Go back to prices step from validation error"""
    context.user_data["gs_wizard_step"] = "prices"
    await _show_wizard_prices_step(update, context)


async def handle_gs_back_to_connector(update: Update, context: Context) -> None:
    """Go back to connector selection step"""
    context.user_data["gs_wizard_step"] = "connector_name"
    await _show_wizard_connector_step(update, context)


async def handle_gs_back_to_pair(update: Update, context: Context) -> None:
    """Go back to trading pair step"""
    context.user_data["gs_wizard_step"] = "trading_pair"
    await _show_wizard_pair_step(update, context)


async def handle_gs_back_to_side(update: Update, context: Context) -> None:
    """Go back to side selection step"""
    context.user_data["gs_wizard_step"] = "side"
    await _show_wizard_side_step(update, context)


async def handle_gs_back_to_leverage(update: Update, context: Context) -> None:
    """Go back to leverage step (or side step for spot exchanges)"""
    config = get_controller_config(context)
    connector = config.get("connector_name", "")

    # If spot exchange, go back to side step instead
    if not connector.endswith("_perpetual"):
        context.user_data["gs_wizard_step"] = "side"
        await _show_wizard_side_step(update, context)
    else:
        context.user_data["gs_wizard_step"] = "leverage"
        await _show_wizard_leverage_step(update, context)


async def handle_gs_back_to_amount(update: Update, context: Context) -> None:
    """Go back to amount step"""
    context.user_data["gs_wizard_step"] = "total_amount_quote"
    # Clear cached market data to avoid showing stale chart
    context.user_data.pop("gs_current_price", None)
    context.user_data.pop("gs_candles", None)
    await _show_wizard_amount_step(update, context)


async def handle_gs_interval_change(
    update: Update, context: Context, interval: str
) -> None:
    """Handle interval change for chart - refetch candles with new interval"""
    query = update.callback_query

    # Clear cached candles to force refetch
    context.user_data.pop("gs_candles", None)
    context.user_data["gs_chart_interval"] = interval

    # Redisplay prices step with new interval
    await _show_wizard_prices_step(update, context, interval=interval)


async def _show_wizard_take_profit_step(update: Update, context: Context) -> None:
    """Wizard Step 7: Take Profit Configuration"""
    query = update.callback_query
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = "📈 LONG" if config.get("side") == SIDE_LONG else "📉 SHORT"

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "take_profit"

    keyboard = [
        [
            InlineKeyboardButton(text="0.01%", callback_data="bots:gs_tp:0.0001"),
            InlineKeyboardButton(text="0.02%", callback_data="bots:gs_tp:0.0002"),
            InlineKeyboardButton(text="0.05%", callback_data="bots:gs_tp:0.0005"),
        ],
        [
            InlineKeyboardButton(text="0.1%", callback_data="bots:gs_tp:0.001"),
            InlineKeyboardButton(text="0.2%", callback_data="bots:gs_tp:0.002"),
            InlineKeyboardButton(text="0.5%", callback_data="bots:gs_tp:0.005"),
        ],
        [InlineKeyboardButton(text="✕ Cancel", callback_data="bots:main_menu")],
    ]

    message_text = (
        r"*📈 Grid Strike \- New Config*" + "\n\n"
        f"🏦 *Connector:* `{escape_markdown_v2(connector)}`" + "\n"
        f"🔗 *Pair:* `{escape_markdown_v2(pair)}`" + "\n"
        f"🎯 *Side:* `{side}` \\| ⚡ *Leverage:* `{config.get('leverage', 1)}x`" + "\n"
        f"💰 *Amount:* `{config.get('total_amount_quote', 0):,.0f}`" + "\n"
        f"📊 *Grid:* `{config.get('start_price', 0):,.6g}` \\- `{config.get('end_price', 0):,.6g}`"
        + "\n\n"
        r"*Step 7/7:* 🎯 Take Profit" + "\n\n"
        r"Select or type take profit % \(e\.g\. `0\.4` for 0\.4%\):"
    )

    # Delete photo message and send text message
    try:
        await query.message.delete()
    except:
        pass

    msg = await context.bot.send_message(
        chat_id=query.message.chat.id,
        text=message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )

    context.user_data["gs_wizard_message_id"] = msg.message_id
    context.user_data["gs_wizard_chat_id"] = query.message.chat.id


def _cleanup_wizard_state(context) -> None:
    """Clean up wizard-related state"""
    keys_to_remove = [
        "gs_wizard_step",
        "gs_wizard_message_id",
        "gs_wizard_chat_id",
        "gs_current_price",
        "gs_candles",
        "gs_chart_message_id",
        "gs_market_data_ready",
        "gs_market_data_error",
        "gs_chart_interval",
        "gs_candles_interval",
    ]
    for key in keys_to_remove:
        context.user_data.pop(key, None)
    clear_bots_state(context)


async def _background_fetch_market_data(
    context, config: dict, chat_id: int = None
) -> None:
    """Background task to fetch market data while user continues with wizard"""
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")

    if not connector or not pair:
        return

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

        # Fetch current price
        current_price = await fetch_current_price(client, connector, pair)

        if current_price:
            context.user_data["gs_current_price"] = current_price

            # Fetch candles (5m, 420 records) - consistent with default interval
            candles = await fetch_candles(
                client, connector, pair, interval="5m", max_records=420
            )
            context.user_data["gs_candles"] = candles
            context.user_data["gs_candles_interval"] = "5m"
            context.user_data["gs_market_data_ready"] = True

            logger.info(f"Background fetch complete for {pair}: price={current_price}")
        else:
            context.user_data["gs_market_data_error"] = (
                f"Could not fetch price for {pair}"
            )

    except Exception as e:
        logger.error(f"Background fetch error for {pair}: {e}")
        context.user_data["gs_market_data_error"] = str(e)


async def _show_gs_pair_suggestions(
    update: Update,
    context: Context,
    input_pair: str,
    error_msg: str,
    suggestions: list,
    connector: str,
) -> None:
    """Show trading pair suggestions when validation fails in grid strike wizard"""
    await show_pair_suggestions(update, context, GS_WIZARD, error_msg, suggestions)


async def handle_gs_pair_select(
    update: Update, context: Context, trading_pair: str
) -> None:
    """Handle selection of a suggested trading pair in grid strike wizard"""
    config = get_controller_config(context)
    chat_id = update.effective_chat.id

    # Clear old market data
    for key in [
        "gs_current_price",
        "gs_candles",
        "gs_candles_interval",
        "gs_natr",
        "gs_trading_rules",
        "gs_theoretical_grid",
        "gs_market_data_ready",
        "gs_market_data_error",
    ]:
        context.user_data.pop(key, None)

    config["trading_pair"] = trading_pair
    set_controller_config(context, config)

    # Start background fetch of market data
    asyncio.create_task(_background_fetch_market_data(context, config, chat_id))

    # Move to side step
    context.user_data["gs_wizard_step"] = "side"

    # Update the wizard message
    await _update_wizard_message_for_side(update, context)


async def _update_wizard_message_for_side(update: Update, context: Context) -> None:
    """Update wizard message to show side step after pair input"""
    config = get_controller_config(context)
    message_id = context.user_data.get("gs_wizard_message_id")
    chat_id = context.user_data.get("gs_wizard_chat_id")

    if not message_id or not chat_id:
        return

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")

    keyboard = [
        [
            InlineKeyboardButton(text="📈 LONG", callback_data="bots:gs_side:long"),
            InlineKeyboardButton(text="📉 SHORT", callback_data="bots:gs_side:short"),
        ],
        [InlineKeyboardButton(text="✕ Cancel", callback_data="bots:main_menu")],
    ]

    # Determine total steps based on connector type
    is_perp = connector.endswith("_perpetual")
    total_steps = 6 if is_perp else 5

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                rf"*📈 Grid Strike \- Step 3/{total_steps}*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
                + "\n\n"
                r"🎯 *Select Side*"
            ),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
    except Exception as e:
        logger.error(f"Error updating wizard message: {e}")


async def _update_wizard_message_for_prices(update: Update, context: Context) -> None:
    """Trigger prices step after amount input"""
    message_id = context.user_data.get("gs_wizard_message_id")
    chat_id = context.user_data.get("gs_wizard_chat_id")

    if not message_id or not chat_id:
        return

    # Create a fake query object to reuse _show_wizard_prices_step
    class FakeChat:
        def __init__(self, chat_id):
            self.id = chat_id

    class FakeQuery:
        def __init__(self, bot, chat_id, message_id):
            self.message = FakeMessage(bot, chat_id, message_id)

    class FakeMessage:
        def __init__(self, bot, chat_id, message_id):
            self.chat_id = chat_id
            self.message_id = message_id
            self._bot = bot

        async def edit_text(self, text, **kwargs):
            await self._bot.edit_message_text(
                chat_id=self.chat_id, message_id=self.message_id, text=text, **kwargs
            )

        async def delete(self):
            await self._bot.delete_message(
                chat_id=self.chat_id, message_id=self.message_id
            )

    fake_update = type(
        "FakeUpdate",
        (),
        {
            "callback_query": FakeQuery(context.bot, chat_id, message_id),
            "effective_chat": FakeChat(chat_id),
        },
    )()
    await _show_wizard_prices_step(fake_update, context)


async def _update_wizard_message_for_amount(update: Update, context: Context) -> None:
    """Trigger amount step after leverage input"""
    message_id = context.user_data.get("gs_wizard_message_id")
    chat_id = context.user_data.get("gs_wizard_chat_id")

    if not message_id or not chat_id:
        return

    # Create a fake query object to reuse _show_wizard_amount_step
    class FakeChat:
        def __init__(self, chat_id):
            self.id = chat_id

    class FakeQuery:
        def __init__(self, bot, chat_id, message_id):
            self.message = FakeMessage(bot, chat_id, message_id)

    class FakeMessage:
        def __init__(self, bot, chat_id, message_id):
            self.chat_id = chat_id
            self.message_id = message_id
            self._bot = bot

        async def edit_text(self, text, **kwargs):
            await self._bot.edit_message_text(
                chat_id=self.chat_id, message_id=self.message_id, text=text, **kwargs
            )

        async def delete(self):
            await self._bot.delete_message(
                chat_id=self.chat_id, message_id=self.message_id
            )

    fake_update = type(
        "FakeUpdate",
        (),
        {
            "callback_query": FakeQuery(context.bot, chat_id, message_id),
            "effective_chat": FakeChat(chat_id),
        },
    )()
    await _show_wizard_amount_step(fake_update, context)
