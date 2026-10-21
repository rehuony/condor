"""Legacy single-screen config form with field editing and connector selection."""

import logging

from condor.telegram.transport import as_input_file
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from utils.telegram_formatters import escape_markdown_v2

from .._shared import (
    GRID_STRIKE_DEFAULTS,
    GRID_STRIKE_FIELD_ORDER,
    GRID_STRIKE_FIELDS,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_LIMIT_MAKER,
    ORDER_TYPE_MARKET,
    SIDE_LONG,
    SIDE_SHORT,
    calculate_auto_prices,
    clean_config_for_save,
    clear_bots_state,
    fetch_candles,
    fetch_current_price,
    format_config_field_value,
    generate_candles_chart,
    generate_config_id,
    get_available_cex_connectors,
    get_bots_client,
    get_controller_config,
    init_new_controller_config,
    set_controller_config,
)

logger = logging.getLogger(__name__)


async def show_config_form(update: Update, context: Context) -> None:
    """Show the configuration form with current values (legacy/edit mode)"""
    query = update.callback_query
    config = get_controller_config(context)

    if not config:
        config = init_new_controller_config(context, "grid_strike")

    # Build the form display
    lines = [r"*Grid Strike Configuration*", ""]

    # Show current values
    for field_name in GRID_STRIKE_FIELD_ORDER:
        field_info = GRID_STRIKE_FIELDS[field_name]
        label = field_info["label"]

        # Get value, handling nested triple_barrier_config
        if field_name == "take_profit":
            value = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)
        elif field_name == "open_order_type":
            value = config.get("triple_barrier_config", {}).get(
                "open_order_type", ORDER_TYPE_LIMIT_MAKER
            )
        elif field_name == "take_profit_order_type":
            value = config.get("triple_barrier_config", {}).get(
                "take_profit_order_type", ORDER_TYPE_LIMIT_MAKER
            )
        else:
            value = config.get(field_name, "")

        formatted_value = format_config_field_value(field_name, value)
        required = "\\*" if field_info.get("required") else ""

        lines.append(
            f"*{escape_markdown_v2(label)}*{required}: `{escape_markdown_v2(formatted_value)}`"
        )

    lines.append("")
    lines.append(r"_Tap a button to edit a field\. \* \= required_")

    # Build keyboard with field buttons
    keyboard = []

    # Row 1: ID and Connector
    keyboard.append(
        [
            InlineKeyboardButton(text="ID", callback_data="bots:set_field:id"),
            InlineKeyboardButton(
                text="Connector", callback_data="bots:set_field:connector_name"
            ),
            InlineKeyboardButton(
                text="Pair", callback_data="bots:set_field:trading_pair"
            ),
        ]
    )

    # Row 2: Side, Leverage, Position Mode
    keyboard.append(
        [
            InlineKeyboardButton(text="Side", callback_data="bots:toggle_side"),
            InlineKeyboardButton(
                text="Leverage", callback_data="bots:set_field:leverage"
            ),
            InlineKeyboardButton(
                text="Pos Mode", callback_data="bots:toggle_position_mode"
            ),
        ]
    )

    # Row 3: Amount and Prices
    keyboard.append(
        [
            InlineKeyboardButton(
                text="Amount", callback_data="bots:set_field:total_amount_quote"
            ),
            InlineKeyboardButton(
                text="Start", callback_data="bots:set_field:start_price"
            ),
            InlineKeyboardButton(text="End", callback_data="bots:set_field:end_price"),
        ]
    )

    # Row 4: Limit Price and Order Settings
    keyboard.append(
        [
            InlineKeyboardButton(
                text="Limit", callback_data="bots:set_field:limit_price"
            ),
            InlineKeyboardButton(
                text="Max Orders", callback_data="bots:set_field:max_open_orders"
            ),
            InlineKeyboardButton(
                text="Min Spread",
                callback_data="bots:set_field:min_spread_between_orders",
            ),
        ]
    )

    # Row 5: Take Profit and Order Types
    keyboard.append(
        [
            InlineKeyboardButton(
                text="Take Profit", callback_data="bots:set_field:take_profit"
            ),
            InlineKeyboardButton(
                text="Open Type", callback_data="bots:cycle_order_type:open"
            ),
            InlineKeyboardButton(
                text="TP Type", callback_data="bots:cycle_order_type:tp"
            ),
        ]
    )

    # Row 6: Actions
    keyboard.append(
        [
            InlineKeyboardButton(text="Save Config", callback_data="bots:save_config"),
            InlineKeyboardButton(
                text="Cancel", callback_data="bots:controller_configs"
            ),
        ]
    )

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await query.message.edit_text(
        "\n".join(lines), parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_set_field(update: Update, context: Context, field_name: str) -> None:
    """Prompt user to enter a value for a field

    Args:
        update: Telegram update
        context: Telegram context
        field_name: Name of the field to edit
    """
    query = update.callback_query

    # Special handling for connector_name - show button selector
    if field_name == "connector_name":
        await show_connector_selector(update, context)
        return

    field_info = GRID_STRIKE_FIELDS.get(field_name, {})
    label = field_info.get("label", field_name)
    hint = field_info.get("hint", "")
    field_type = field_info.get("type", "str")

    # Set state for text input
    context.user_data["bots_state"] = f"set_field:{field_name}"
    context.user_data["editing_controller_field"] = field_name

    # Get current value
    config = get_controller_config(context)
    if field_name == "take_profit":
        current = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)
    else:
        current = config.get(field_name, "")

    current_str = format_config_field_value(field_name, current)

    message = (
        f"*Set {escape_markdown_v2(label)}*\n\n"
        f"Current: `{escape_markdown_v2(current_str)}`\n\n"
    )

    if hint:
        message += f"_Hint: {escape_markdown_v2(hint)}_\n\n"

    message += r"Type the new value or tap Cancel\."

    keyboard = [
        [InlineKeyboardButton(text="Cancel", callback_data="bots:edit_config_back")]
    ]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await query.message.edit_text(
        message, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def show_connector_selector(update: Update, context: Context) -> None:
    """Show connector selection keyboard with available CEX connectors"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, server_name = await get_bots_client(chat_id, context.user_data)

        # Get available CEX connectors (with cache)
        cex_connectors = await get_available_cex_connectors(
            context.user_data, client, server_name=server_name
        )

        if not cex_connectors:
            await query.answer("No CEX connectors configured", show_alert=True)
            return

        # Build connector buttons (2 per row)
        keyboard = []
        row = []

        for connector in cex_connectors:
            row.append(
                InlineKeyboardButton(
                    text=connector, callback_data=f"bots:select_connector:{connector}"
                )
            )
            if len(row) == 2:
                keyboard.append(row)
                row = []

        if row:
            keyboard.append(row)

        keyboard.append(
            [InlineKeyboardButton(text="Cancel", callback_data="bots:edit_config_back")]
        )
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        config = get_controller_config(context)
        current = config.get("connector_name", "") or "Not set"

        await query.message.edit_text(
            r"*Select Connector*" + "\n\n"
            f"Current: `{escape_markdown_v2(current)}`\n\n"
            r"Choose an exchange from your configured connectors:",
            parse_mode="MarkdownV2",
            reply_markup=reply_markup,
        )

    except Exception as e:
        logger.error(f"Error showing connector selector: {e}", exc_info=True)
        await query.answer(f"Error: {str(e)[:50]}", show_alert=True)


async def handle_select_connector(
    update: Update, context: Context, connector_name: str
) -> None:
    """Handle connector selection from keyboard"""
    query = update.callback_query

    config = get_controller_config(context)
    config["connector_name"] = connector_name
    set_controller_config(context, config)

    await query.answer(f"Connector set to {connector_name}")

    # If we have both connector and trading pair, fetch market data
    if config.get("trading_pair"):
        await fetch_and_apply_market_data(update, context)
    else:
        await show_config_form(update, context)


async def fetch_and_apply_market_data(update: Update, context: Context) -> None:
    """Fetch current price and candles, apply auto-pricing, show chart"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    connector = config.get("connector_name")
    pair = config.get("trading_pair")
    side = config.get("side", SIDE_LONG)

    if not connector or not pair:
        await show_config_form(update, context)
        return

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

        # Show loading message
        await query.message.edit_text(
            f"Fetching market data for *{escape_markdown_v2(pair)}*\\.\\.\\.",
            parse_mode="MarkdownV2",
        )

        # Fetch current price
        current_price = await fetch_current_price(client, connector, pair)

        if current_price:
            # Cache the current price
            context.user_data["grid_strike_current_price"] = current_price

            # Calculate auto prices
            start, end, limit = calculate_auto_prices(current_price, side)
            config["start_price"] = start
            config["end_price"] = end
            config["limit_price"] = limit

            # Generate auto ID with sequence number
            existing_configs = context.user_data.get("controller_configs_list", [])
            config["id"] = generate_config_id(
                connector, pair, existing_configs=existing_configs
            )

            set_controller_config(context, config)

            # Fetch candles for chart
            candles = await fetch_candles(
                client, connector, pair, interval="5m", max_records=420
            )

            if candles:
                # Generate and send chart
                chart_bytes = generate_candles_chart(
                    candles,
                    pair,
                    start_price=start,
                    end_price=end,
                    limit_price=limit,
                    current_price=current_price,
                )

                # Send chart as photo
                await query.message.answer_photo(
                    photo=as_input_file(chart_bytes),
                    caption=(
                        f"*{escape_markdown_v2(pair)}* Grid Zone\n\n"
                        f"Current: `{current_price:,.4f}`\n"
                        f"Start: `{start:,.4f}` \\(\\-2%\\)\n"
                        f"End: `{end:,.4f}` \\(\\+2%\\)\n"
                        f"Limit: `{limit:,.4f}`"
                    ),
                    parse_mode="MarkdownV2",
                )
            else:
                # No candles, just show price info
                await query.message.answer(
                    f"*{escape_markdown_v2(pair)}* Market Data\n\n"
                    f"Current Price: `{current_price:,.4f}`\n"
                    f"Auto\\-calculated grid:\n"
                    f"  Start: `{start:,.4f}`\n"
                    f"  End: `{end:,.4f}`\n"
                    f"  Limit: `{limit:,.4f}`",
                    parse_mode="MarkdownV2",
                )
        else:
            await query.message.answer(
                f"Could not fetch price for {pair}. Please set prices manually.",
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"Error fetching market data: {e}", exc_info=True)
        await query.message.answer(
            f"Error fetching market data: {str(e)[:100]}", parse_mode="HTML"
        )

    # Show the config form
    keyboard = [
        [
            InlineKeyboardButton(
                text="Continue Editing", callback_data="bots:edit_config_back"
            )
        ]
    ]
    await query.message.answer(
        "Tap to continue editing configuration\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_toggle_side(update: Update, context: Context) -> None:
    """Toggle the side between LONG and SHORT"""
    query = update.callback_query
    config = get_controller_config(context)

    current_side = config.get("side", SIDE_LONG)
    new_side = SIDE_SHORT if current_side == SIDE_LONG else SIDE_LONG
    config["side"] = new_side

    # Recalculate prices if we have a current price cached
    current_price = context.user_data.get("grid_strike_current_price")
    if current_price:
        start, end, limit = calculate_auto_prices(current_price, new_side)
        config["start_price"] = start
        config["end_price"] = end
        config["limit_price"] = limit

        # Regenerate ID with sequence number
        if config.get("connector_name") and config.get("trading_pair"):
            existing_configs = context.user_data.get("controller_configs_list", [])
            config["id"] = generate_config_id(
                config["connector_name"],
                config["trading_pair"],
                existing_configs=existing_configs,
            )

    set_controller_config(context, config)

    # Refresh the form
    await show_config_form(update, context)


async def handle_toggle_position_mode(update: Update, context: Context) -> None:
    """Toggle the position mode between HEDGE and ONEWAY"""
    query = update.callback_query
    config = get_controller_config(context)

    current_mode = config.get("position_mode", "ONEWAY")
    new_mode = "ONEWAY" if current_mode == "HEDGE" else "HEDGE"
    config["position_mode"] = new_mode

    set_controller_config(context, config)

    # Refresh the form
    await show_config_form(update, context)


async def handle_cycle_order_type(
    update: Update, context: Context, order_type_key: str
) -> None:
    """Cycle the order type between Market, Limit, and Limit Maker

    Args:
        update: Telegram update
        context: Telegram context
        order_type_key: 'open' for open_order_type, 'tp' for take_profit_order_type
    """
    query = update.callback_query
    config = get_controller_config(context)

    # Determine which field to update
    field_name = (
        "open_order_type" if order_type_key == "open" else "take_profit_order_type"
    )

    # Get current value
    if "triple_barrier_config" not in config:
        config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS[
            "triple_barrier_config"
        ].copy()

    current_type = config["triple_barrier_config"].get(
        field_name, ORDER_TYPE_LIMIT_MAKER
    )

    # Cycle: Limit Maker -> Market -> Limit -> Limit Maker
    order_cycle = [ORDER_TYPE_LIMIT_MAKER, ORDER_TYPE_MARKET, ORDER_TYPE_LIMIT]
    try:
        current_index = order_cycle.index(current_type)
        next_index = (current_index + 1) % len(order_cycle)
    except ValueError:
        next_index = 0

    new_type = order_cycle[next_index]
    config["triple_barrier_config"][field_name] = new_type

    set_controller_config(context, config)

    # Refresh the form
    await show_config_form(update, context)


async def process_field_input(
    update: Update, context: Context, user_input: str
) -> None:
    """Process user input for a field

    Args:
        update: Telegram update
        context: Telegram context
        user_input: The text the user entered
    """
    chat_id = update.effective_chat.id
    field_name = context.user_data.get("editing_controller_field")

    if not field_name:
        await update.message.answer("No field selected. Please try again.")
        return

    field_info = GRID_STRIKE_FIELDS.get(field_name, {})
    field_type = field_info.get("type", "str")
    label = field_info.get("label", field_name)

    config = get_controller_config(context)

    try:
        # Parse the value based on type
        if field_type == "int":
            value = int(user_input)
        elif field_type == "float":
            value = float(user_input)
        else:
            value = user_input.strip()

        # Set the value
        if field_name == "take_profit":
            if "triple_barrier_config" not in config:
                config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS[
                    "triple_barrier_config"
                ].copy()
            config["triple_barrier_config"]["take_profit"] = value
        else:
            config[field_name] = value

        set_controller_config(context, config)

        # Clear field editing state
        context.user_data.pop("editing_controller_field", None)
        context.user_data["bots_state"] = "editing_config"

        # Show success
        await update.message.answer(f"{label} set to: {value}", parse_mode="HTML")

        # If trading_pair was set and we have a connector, fetch market data
        if field_name == "trading_pair" and config.get("connector_name"):
            # Create a fake callback query context for fetch_and_apply_market_data
            keyboard = [
                [
                    InlineKeyboardButton(
                        text="Fetching Market Data...", callback_data="bots:noop"
                    )
                ]
            ]
            msg = await update.message.answer(
                "Fetching market data\\.\\.\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )

            try:
                client, _ = await get_bots_client(chat_id, context.user_data)
                connector = config.get("connector_name")
                pair = config.get("trading_pair")
                side = config.get("side", SIDE_LONG)

                # Fetch current price
                current_price = await fetch_current_price(client, connector, pair)

                if current_price:
                    # Cache and calculate
                    context.user_data["grid_strike_current_price"] = current_price
                    start, end, limit = calculate_auto_prices(current_price, side)
                    config["start_price"] = start
                    config["end_price"] = end
                    config["limit_price"] = limit
                    existing_configs = context.user_data.get(
                        "controller_configs_list", []
                    )
                    config["id"] = generate_config_id(
                        connector, pair, existing_configs=existing_configs
                    )
                    set_controller_config(context, config)

                    # Fetch candles
                    candles = await fetch_candles(
                        client, connector, pair, interval="5m", max_records=420
                    )

                    if candles:
                        chart_bytes = generate_candles_chart(
                            candles,
                            pair,
                            start_price=start,
                            end_price=end,
                            limit_price=limit,
                            current_price=current_price,
                        )
                        await update.message.answer_photo(
                            photo=as_input_file(chart_bytes),
                            caption=(
                                f"*{escape_markdown_v2(pair)}* Grid Zone\n\n"
                                f"Current: `{current_price:,.4f}`\n"
                                f"Start: `{start:,.4f}` \\(\\-2%\\)\n"
                                f"End: `{end:,.4f}` \\(\\+2%\\)\n"
                                f"Limit: `{limit:,.4f}`"
                            ),
                            parse_mode="MarkdownV2",
                        )
                    else:
                        await update.message.answer(
                            f"*{escape_markdown_v2(pair)}* prices auto\\-calculated\\.\n\n"
                            f"Current: `{current_price:,.4f}`",
                            parse_mode="MarkdownV2",
                        )
                else:
                    await update.message.answer(
                        f"Could not fetch price for {pair}. Set prices manually."
                    )

            except Exception as e:
                logger.error(f"Error fetching market data: {e}", exc_info=True)
                await update.message.answer(
                    f"Error fetching market data: {str(e)[:50]}"
                )

        # Show the form again
        keyboard = [
            [
                InlineKeyboardButton(
                    text="Continue Editing", callback_data="bots:edit_config_back"
                )
            ]
        ]
        await update.message.answer(
            "Tap to continue editing configuration\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )

    except ValueError as e:
        await update.message.answer(
            f"Invalid value for {label}. Expected {field_type}. Please try again."
        )


async def handle_save_config(update: Update, context: Context) -> None:
    """Save the current config to the backend"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    # Validate required fields
    missing = []
    for field_name in GRID_STRIKE_FIELD_ORDER:
        field_info = GRID_STRIKE_FIELDS[field_name]
        if field_info.get("required"):
            if field_name == "take_profit":
                value = config.get("triple_barrier_config", {}).get("take_profit")
            else:
                value = config.get(field_name)

            if value is None or value == "" or value == 0:
                missing.append(field_info["label"])

    if missing:
        missing_str = ", ".join(missing)
        await query.answer(f"Missing required fields: {missing_str}", show_alert=True)
        return

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

        # Save to backend using config id as the config_name
        config_name = config.get("id", "")
        result = await client.controllers.create_or_update_controller_config(
            config_name, clean_config_for_save(config)
        )

        # Clear state
        clear_bots_state(context)

        keyboard = [
            [
                InlineKeyboardButton(
                    text="Create Another", callback_data="bots:new_grid_strike"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Back to Configs", callback_data="bots:controller_configs"
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        config_id = config.get("id", "unknown")
        await query.message.edit_text(
            f"*Config Saved\\!*\n\n"
            f"Controller `{escape_markdown_v2(config_id)}` has been saved successfully\\.",
            parse_mode="MarkdownV2",
            reply_markup=reply_markup,
        )

    except Exception as e:
        logger.error(f"Error saving config: {e}", exc_info=True)
        await query.answer(f"Failed to save: {str(e)[:100]}", show_alert=True)


async def handle_edit_config(
    update: Update, context: Context, config_index: int
) -> None:
    """Load an existing config for editing

    Args:
        update: Telegram update
        context: Telegram context
        config_index: Index in the configs list
    """
    query = update.callback_query
    configs_list = context.user_data.get("controller_configs_list", [])

    if config_index >= len(configs_list):
        await query.answer("Config not found", show_alert=True)
        return

    config = configs_list[config_index].copy()
    set_controller_config(context, config)
    context.user_data["bots_state"] = "editing_config"

    await show_config_form(update, context)
