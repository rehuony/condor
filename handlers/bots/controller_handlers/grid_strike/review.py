"""Grid Strike wizard review screen, field edits and save."""

import logging

from condor.telegram.errors import BadRequest
from condor.telegram.transport import as_input_file
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from utils.telegram_formatters import escape_markdown_v2, format_error_message

from ..._shared import (
    GRID_STRIKE_DEFAULTS,
    ORDER_TYPE_LIMIT_MAKER,
    SIDE_LONG,
    clean_config_for_save,
    generate_candles_chart,
    get_bots_client,
    get_controller_config,
    set_controller_config,
)
from ...controllers.grid_strike.grid_analysis import (
    generate_theoretical_grid,
)
from ..grid_strike.steps import (
    _calculate_min_order_amount,
    _cleanup_wizard_state,
    _show_wizard_prices_step,
)

logger = logging.getLogger(__name__)


async def handle_gs_wizard_take_profit(
    update: Update, context: Context, tp: float
) -> None:
    """Handle take profit selection and show final review"""
    query = update.callback_query
    config = get_controller_config(context)

    if "triple_barrier_config" not in config:
        config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS[
            "triple_barrier_config"
        ].copy()
    config["triple_barrier_config"]["take_profit"] = tp
    set_controller_config(context, config)

    # Move to review step
    context.user_data["gs_wizard_step"] = "review"
    await _show_wizard_review_step(update, context)


async def _show_wizard_review_step(update: Update, context: Context) -> None:
    """Final Review Step with copyable config format"""
    query = update.callback_query
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = "LONG" if config.get("side") == SIDE_LONG else "SHORT"
    leverage = config.get("leverage", 1)
    position_mode = config.get("position_mode", "ONEWAY")
    amount = config.get("total_amount_quote", 0)
    start_price = config.get("start_price", 0)
    end_price = config.get("end_price", 0)
    limit_price = config.get("limit_price", 0)
    tp = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)
    open_order_type = config.get("triple_barrier_config", {}).get(
        "open_order_type", ORDER_TYPE_LIMIT_MAKER
    )
    tp_order_type = config.get("triple_barrier_config", {}).get(
        "take_profit_order_type", ORDER_TYPE_LIMIT_MAKER
    )
    keep_position = config.get("keep_position", True)
    activation_bounds = config.get("activation_bounds", 0.01)
    config_id = config.get("id", "")
    max_open_orders = config.get("max_open_orders", 3)
    max_orders_per_batch = config.get("max_orders_per_batch", 1)
    order_frequency = config.get("order_frequency", 3)
    min_order_amount = config.get("min_order_amount_quote", 6)
    min_spread = config.get("min_spread_between_orders", 0.0001)
    coerce_tp_to_step = config.get("coerce_tp_to_step", False)

    # Delete previous chart if exists
    chart_msg_id = context.user_data.pop("gs_chart_message_id", None)
    if chart_msg_id:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat.id, message_id=chart_msg_id
            )
        except:
            pass

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "review"

    # Build copyable config block with real YAML field names
    side_value = config.get("side", SIDE_LONG)
    config_block = (
        f"id: {config_id}\n"
        f"connector_name: {connector}\n"
        f"trading_pair: {pair}\n"
        f"side: {side_value}\n"
        f"leverage: {leverage}\n"
        f"position_mode: {position_mode}\n"
        f"total_amount_quote: {amount:.0f}\n"
        f"start_price: {start_price:.6g}\n"
        f"end_price: {end_price:.6g}\n"
        f"limit_price: {limit_price:.6g}\n"
        f"take_profit: {tp}\n"
        f"open_order_type: {open_order_type}\n"
        f"take_profit_order_type: {tp_order_type}\n"
        f"coerce_tp_to_step: {str(coerce_tp_to_step).lower()}\n"
        f"keep_position: {str(keep_position).lower()}\n"
        f"activation_bounds: {activation_bounds}\n"
        f"max_open_orders: {max_open_orders}\n"
        f"max_orders_per_batch: {max_orders_per_batch}\n"
        f"order_frequency: {order_frequency}\n"
        f"min_order_amount_quote: {min_order_amount}\n"
        f"min_spread_between_orders: {min_spread}"
    )

    message_text = (
        f"*{escape_markdown_v2(pair)}* \\- Review Config\n\n"
        f"```\n{config_block}\n```\n\n"
        f"_To edit, send `field: value` lines:_\n"
        f"`leverage: 75`\n"
        f"`total_amount_quote: 1000`"
    )

    keyboard = [
        [
            InlineKeyboardButton(text="✅ Save Config", callback_data="bots:gs_save"),
        ],
        [
            InlineKeyboardButton(text="✕ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    # Handle photo messages - can't edit_text on photos, need to delete and send new
    try:
        if getattr(query.message, "photo", None):
            await query.message.delete()
            msg = await context.bot.send_message(
                chat_id=query.message.chat.id,
                text=message_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )
            context.user_data["gs_wizard_message_id"] = msg.message_id
        else:
            await query.message.edit_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )
    except BadRequest as e:
        # Fallback: delete and send new message
        try:
            await query.message.delete()
        except Exception:
            pass
        msg = await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
        context.user_data["gs_wizard_message_id"] = msg.message_id


async def _update_wizard_message_for_review(update: Update, context: Context) -> None:
    """Update wizard to show review step with copyable config format"""
    message_id = context.user_data.get("gs_wizard_message_id")
    chat_id = context.user_data.get("gs_wizard_chat_id")

    if not message_id or not chat_id:
        return

    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = "LONG" if config.get("side") == SIDE_LONG else "SHORT"
    leverage = config.get("leverage", 1)
    position_mode = config.get("position_mode", "ONEWAY")
    amount = config.get("total_amount_quote", 0)
    start_price = config.get("start_price", 0)
    end_price = config.get("end_price", 0)
    limit_price = config.get("limit_price", 0)
    tp = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)
    open_order_type = config.get("triple_barrier_config", {}).get(
        "open_order_type", ORDER_TYPE_LIMIT_MAKER
    )
    tp_order_type = config.get("triple_barrier_config", {}).get(
        "take_profit_order_type", ORDER_TYPE_LIMIT_MAKER
    )
    keep_position = config.get("keep_position", True)
    activation_bounds = config.get("activation_bounds", 0.01)
    config_id = config.get("id", "")
    max_open_orders = config.get("max_open_orders", 3)
    max_orders_per_batch = config.get("max_orders_per_batch", 1)
    order_frequency = config.get("order_frequency", 3)
    min_order_amount = config.get("min_order_amount_quote", 6)
    min_spread = config.get("min_spread_between_orders", 0.0001)
    coerce_tp_to_step = config.get("coerce_tp_to_step", False)

    # Build copyable config block with real YAML field names
    side_value = config.get("side", SIDE_LONG)
    config_block = (
        f"id: {config_id}\n"
        f"connector_name: {connector}\n"
        f"trading_pair: {pair}\n"
        f"side: {side_value}\n"
        f"leverage: {leverage}\n"
        f"position_mode: {position_mode}\n"
        f"total_amount_quote: {amount:.0f}\n"
        f"start_price: {start_price:.6g}\n"
        f"end_price: {end_price:.6g}\n"
        f"limit_price: {limit_price:.6g}\n"
        f"take_profit: {tp}\n"
        f"open_order_type: {open_order_type}\n"
        f"take_profit_order_type: {tp_order_type}\n"
        f"coerce_tp_to_step: {str(coerce_tp_to_step).lower()}\n"
        f"keep_position: {str(keep_position).lower()}\n"
        f"activation_bounds: {activation_bounds}\n"
        f"max_open_orders: {max_open_orders}\n"
        f"max_orders_per_batch: {max_orders_per_batch}\n"
        f"order_frequency: {order_frequency}\n"
        f"min_order_amount_quote: {min_order_amount}\n"
        f"min_spread_between_orders: {min_spread}"
    )

    message_text = (
        f"*{escape_markdown_v2(pair)}* \\- Review Config\n\n"
        f"```\n{config_block}\n```\n\n"
        f"_To edit, send `field: value` lines:_\n"
        f"`leverage: 75`\n"
        f"`total_amount_quote: 1000`"
    )

    keyboard = [
        [
            InlineKeyboardButton(text="✅ Save Config", callback_data="bots:gs_save"),
        ],
        [
            InlineKeyboardButton(text="✕ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
    except Exception as e:
        logger.error(f"Error updating review message: {e}", exc_info=True)
        logger.debug(f"Message text was: {message_text[:500]}")


async def handle_gs_edit_id(update: Update, context: Context) -> None:
    """Allow user to edit config ID before saving"""
    query = update.callback_query
    config = get_controller_config(context)
    chat_id = query.message.chat.id

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "edit_id"

    current_id = config.get("id", "")

    keyboard = [
        [
            InlineKeyboardButton(
                text=f"Keep: {current_id[:25]}", callback_data="bots:gs_save"
            )
        ],
        [InlineKeyboardButton(text="✕ Cancel", callback_data="bots:gs_review_back")],
    ]

    # Delete current message (could be photo)
    try:
        await query.message.delete()
    except:
        pass

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=r"*Edit Config ID*" + "\n\n"
        f"Current: `{escape_markdown_v2(current_id)}`" + "\n\n"
        r"Type a new ID or tap Keep to use current:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_edit_keep(update: Update, context: Context) -> None:
    """Toggle keep_position setting"""
    query = update.callback_query
    config = get_controller_config(context)

    # Toggle the value
    current = config.get("keep_position", True)
    config["keep_position"] = not current
    context.user_data["controller_config"] = config

    # Go back to review
    await _show_wizard_review_step(update, context)


async def handle_gs_edit_tp(update: Update, context: Context) -> None:
    """Edit take profit"""
    query = update.callback_query
    config = get_controller_config(context)
    chat_id = query.message.chat.id

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "edit_tp"

    current_tp = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)

    keyboard = [
        [InlineKeyboardButton(text="✕ Cancel", callback_data="bots:gs_review_back")],
    ]

    try:
        await query.message.delete()
    except:
        pass

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=r"*Edit Take Profit*" + "\n\n"
        f"Current: `{current_tp*100:.4f}%`" + "\n\n"
        r"Enter new TP \(e\.g\. 0\.03 for 0\.03%\):",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_edit_act(update: Update, context: Context) -> None:
    """Edit activation bounds"""
    query = update.callback_query
    config = get_controller_config(context)
    chat_id = query.message.chat.id

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "edit_act"

    current_act = config.get("activation_bounds", 0.01)

    keyboard = [
        [InlineKeyboardButton(text="✕ Cancel", callback_data="bots:gs_review_back")],
    ]

    try:
        await query.message.delete()
    except:
        pass

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=r"*Edit Activation Bounds*" + "\n\n"
        f"Current: `{current_act*100:.1f}%`" + "\n\n"
        r"Enter new value \(e\.g\. 1 for 1%\):",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_edit_max_orders(update: Update, context: Context) -> None:
    """Edit max open orders"""
    query = update.callback_query
    config = get_controller_config(context)
    chat_id = query.message.chat.id

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "edit_max_orders"

    current = config.get("max_open_orders", 3)

    keyboard = [
        [InlineKeyboardButton(text="✕ Cancel", callback_data="bots:gs_review_back")],
    ]

    try:
        await query.message.delete()
    except:
        pass

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=r"*Edit Max Open Orders*" + "\n\n"
        f"Current: `{current}`" + "\n\n"
        r"Enter new value \(integer\):",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_edit_batch(update: Update, context: Context) -> None:
    """Edit max orders per batch"""
    query = update.callback_query
    config = get_controller_config(context)
    chat_id = query.message.chat.id

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "edit_batch"

    current = config.get("max_orders_per_batch", 1)

    keyboard = [
        [InlineKeyboardButton(text="✕ Cancel", callback_data="bots:gs_review_back")],
    ]

    try:
        await query.message.delete()
    except:
        pass

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=r"*Edit Max Orders Per Batch*" + "\n\n"
        f"Current: `{current}`" + "\n\n"
        r"Enter new value \(integer\):",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_edit_min_amt(update: Update, context: Context) -> None:
    """Edit min order amount"""
    query = update.callback_query
    config = get_controller_config(context)
    chat_id = query.message.chat.id

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "edit_min_amt"

    current = config.get("min_order_amount_quote", 6)

    # Calculate minimum required from trading rules
    current_price = context.user_data.get("gs_current_price", 0)
    trading_rules = context.user_data.get("gs_trading_rules", {})
    required_min = _calculate_min_order_amount(
        current_price, trading_rules, default=6.0
    )

    keyboard = [
        [InlineKeyboardButton(text="✕ Cancel", callback_data="bots:gs_review_back")],
    ]

    try:
        await query.message.delete()
    except:
        pass

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=r"*Edit Min Order Amount*" + "\n\n"
        f"Current: `{current}`\n"
        f"Minimum: `{required_min:.2f}` \\(from trading rules\\)" + "\n\n"
        r"Enter new value:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_edit_spread(update: Update, context: Context) -> None:
    """Edit min spread between orders"""
    query = update.callback_query
    config = get_controller_config(context)
    chat_id = query.message.chat.id

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "edit_spread"

    current = config.get("min_spread_between_orders", 0.0001)

    keyboard = [
        [InlineKeyboardButton(text="✕ Cancel", callback_data="bots:gs_review_back")],
    ]

    try:
        await query.message.delete()
    except:
        pass

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=r"*Edit Min Spread Between Orders*" + "\n\n"
        f"Current: `{current}`" + "\n\n"
        r"Enter new value \(e\.g\. 0\.0002\):",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_save(update: Update, context: Context) -> None:
    """Save the Grid Strike configuration"""
    query = update.callback_query
    config = get_controller_config(context)

    # Validate price ordering before saving
    side = config.get("side", SIDE_LONG)
    start_price = config.get("start_price", 0)
    end_price = config.get("end_price", 0)
    limit_price = config.get("limit_price", 0)

    validation_error = None
    if side == SIDE_LONG:
        if not (limit_price < start_price < end_price):
            validation_error = (
                "Invalid prices for LONG position\\.\n\n"
                "Required: `limit < start < end`\n"
                f"Current: `{limit_price:,.6g}` < `{start_price:,.6g}` < `{end_price:,.6g}`"
            )
    else:  # SHORT
        if not (start_price < end_price < limit_price):
            validation_error = (
                "Invalid prices for SHORT position\\.\n\n"
                "Required: `start < end < limit`\n"
                f"Current: `{start_price:,.6g}` < `{end_price:,.6g}` < `{limit_price:,.6g}`"
            )

    if validation_error:
        await query.answer("Invalid price configuration", show_alert=True)
        keyboard = [
            [
                InlineKeyboardButton(
                    text="✏️ Edit Prices", callback_data="bots:gs_back_to_prices"
                )
            ],
            [InlineKeyboardButton(text="✕ Cancel", callback_data="bots:main_menu")],
        ]
        try:
            await query.message.delete()
        except:
            pass
        msg = await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=f"⚠️ *Price Validation Error*\n\n{validation_error}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
        context.user_data["gs_wizard_message_id"] = msg.message_id
        context.user_data["gs_wizard_chat_id"] = query.message.chat.id
        return

    config_id = config.get("id", "")
    chat_id = query.message.chat.id

    # Delete the current message (could be photo or text)
    try:
        await query.message.delete()
    except:
        pass

    # Send saving status
    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"Saving configuration `{escape_markdown_v2(config_id)}`\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        result = await client.controllers.create_or_update_controller_config(
            config_id, clean_config_for_save(config)
        )

        # Clean up wizard state
        _cleanup_wizard_state(context)

        keyboard = [
            [
                InlineKeyboardButton(
                    text="➕ Create Another", callback_data="bots:new_grid_strike"
                )
            ],
            [
                InlineKeyboardButton(
                    text="« Back to Configs", callback_data="bots:controller_configs"
                )
            ],
        ]

        await status_msg.edit_text(
            r"*Config Saved\!*" + "\n\n"
            f"Controller `{escape_markdown_v2(config_id)}` saved successfully\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )

    except Exception as e:
        logger.error(f"Error saving config: {e}", exc_info=True)
        keyboard = [
            [InlineKeyboardButton(text="🔁 Try Again", callback_data="bots:gs_save")],
            [InlineKeyboardButton(text="« Back", callback_data="bots:gs_review_back")],
        ]
        await status_msg.edit_text(
            format_error_message(f"Failed to save: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )


async def handle_gs_review_back(update: Update, context: Context) -> None:
    """Go back to prices step (main configuration screen)"""
    context.user_data["gs_wizard_step"] = "prices"
    await _show_wizard_prices_step(update, context)


async def _update_wizard_message_for_prices_after_edit(
    update: Update, context: Context
) -> None:
    """Update prices display after editing prices - regenerate chart with new prices and grid analysis"""
    config = get_controller_config(context)
    message_id = context.user_data.get("gs_wizard_message_id")
    chat_id = context.user_data.get("gs_wizard_chat_id")

    if not message_id or not chat_id:
        return

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = config.get("side", SIDE_LONG)
    side_str = "📈 LONG" if side == SIDE_LONG else "📉 SHORT"
    start = config.get("start_price", 0)
    end = config.get("end_price", 0)
    limit = config.get("limit_price", 0)
    current_price = context.user_data.get("gs_current_price", 0)
    candles = context.user_data.get("gs_candles")
    interval = context.user_data.get("gs_chart_interval", "5m")
    total_amount = config.get("total_amount_quote", 1000)
    min_spread = config.get("min_spread_between_orders", 0.0001)
    take_profit = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)
    min_order_amount = config.get("min_order_amount_quote", 6)
    natr = context.user_data.get("gs_natr")
    trading_rules = context.user_data.get("gs_trading_rules", {})

    # Regenerate theoretical grid with updated parameters
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

    # Build interval buttons with current one highlighted
    interval_options = ["1m", "5m", "15m", "1h", "4h"]
    interval_row = []
    for opt in interval_options:
        label = f"✓ {opt}" if opt == interval else opt
        interval_row.append(
            InlineKeyboardButton(text=label, callback_data=f"bots:gs_interval:{opt}")
        )

    keyboard = [
        interval_row,
        [
            InlineKeyboardButton(text="💾 Save Config", callback_data="bots:gs_save"),
        ],
        [InlineKeyboardButton(text="✕ Cancel", callback_data="bots:main_menu")],
    ]

    # Get config values
    max_open_orders = config.get("max_open_orders", 3)
    order_frequency = config.get("order_frequency", 3)
    leverage = config.get("leverage", 1)
    position_mode = config.get("position_mode", "ONEWAY")
    coerce_tp_to_step = config.get("coerce_tp_to_step", False)
    activation_bounds = config.get("activation_bounds", 0.01)
    side_value = config.get("side", SIDE_LONG)
    side_str = "LONG" if side_value == SIDE_LONG else "SHORT"

    # Grid analysis info
    grid_valid = "✓" if grid.get("valid") else "⚠️"
    natr_pct = f"{natr*100:.2f}%" if natr else "N/A"
    range_pct = f"{grid.get('grid_range_pct', 0):.2f}%"

    # Build config text with individually copyable key=value params
    config_text = (
        f"*{escape_markdown_v2(pair)}* {side_str}\n"
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

    try:
        # Delete old message (which is a photo)
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass

        # Get candles list
        candles_list = candles.get("data", []) if isinstance(candles, dict) else candles

        # Generate new chart with updated prices
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

            # Send new photo with updated caption
            msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=as_input_file(chart_bytes),
                caption=config_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )

            # Update stored message ID
            context.user_data["gs_wizard_message_id"] = msg.message_id
        else:
            # No chart - send text message
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=config_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )
            context.user_data["gs_wizard_message_id"] = msg.message_id

    except Exception as e:
        logger.error(f"Error updating prices message: {e}", exc_info=True)


async def _update_wizard_caption_only(
    update: Update, context: Context, warning_msg: str = None
) -> None:
    """
    Update only the caption of the chart message without regenerating the chart.

    Use this when changing fields that don't affect the visual representation
    (e.g., min_order_amount, take_profit, activation_bounds, etc.)
    """
    config = get_controller_config(context)
    message_id = context.user_data.get("gs_wizard_message_id")
    chat_id = context.user_data.get("gs_wizard_chat_id")

    if not message_id or not chat_id:
        return

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = config.get("side", SIDE_LONG)
    side_value = config.get("side", SIDE_LONG)
    side_str = "LONG" if side_value == SIDE_LONG else "SHORT"
    start = config.get("start_price", 0)
    end = config.get("end_price", 0)
    limit = config.get("limit_price", 0)
    current_price = context.user_data.get("gs_current_price", 0)
    interval = context.user_data.get("gs_chart_interval", "5m")
    total_amount = config.get("total_amount_quote", 1000)
    min_spread = config.get("min_spread_between_orders", 0.0001)
    take_profit = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)
    min_order_amount = config.get("min_order_amount_quote", 6)
    natr = context.user_data.get("gs_natr")
    trading_rules = context.user_data.get("gs_trading_rules", {})

    # Get config values
    max_open_orders = config.get("max_open_orders", 3)
    leverage = config.get("leverage", 1)
    position_mode = config.get("position_mode", "ONEWAY")
    coerce_tp_to_step = config.get("coerce_tp_to_step", False)
    activation_bounds = config.get("activation_bounds", 0.01)

    # Regenerate theoretical grid with updated parameters
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

    # Build interval buttons with current one highlighted
    interval_options = ["1m", "5m", "15m", "1h", "4h"]
    interval_row = []
    for opt in interval_options:
        label = f"✓ {opt}" if opt == interval else opt
        interval_row.append(
            InlineKeyboardButton(text=label, callback_data=f"bots:gs_interval:{opt}")
        )

    keyboard = [
        interval_row,
        [
            InlineKeyboardButton(text="💾 Save Config", callback_data="bots:gs_save"),
        ],
        [InlineKeyboardButton(text="✕ Cancel", callback_data="bots:main_menu")],
    ]

    # Grid analysis info
    grid_valid = "✓" if grid.get("valid") else "⚠️"
    natr_pct = f"{natr*100:.2f}%" if natr else "N/A"
    range_pct = f"{grid.get('grid_range_pct', 0):.2f}%"

    # Build config text with individually copyable key=value params
    config_text = (
        f"*{escape_markdown_v2(pair)}* {side_str}\n"
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

    # Add user warning message if provided
    if warning_msg:
        config_text += f"\n\n⚠️ {escape_markdown_v2(warning_msg)}"

    config_text += "\n\n_Edit: `field=value`_"

    try:
        # Try to edit the caption of the existing photo message
        await context.bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=config_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
    except Exception as e:
        # If editing caption fails (e.g., message is text not photo), fall back to full update
        logger.warning(f"Caption edit failed, falling back to full update: {e}")
        await _update_wizard_message_for_prices_after_edit(update, context)


async def handle_gs_edit_price(
    update: Update, context: Context, price_type: str
) -> None:
    """Handle price editing request"""
    query = update.callback_query
    config = get_controller_config(context)

    price_map = {
        "start": ("start_price", "Start Price"),
        "end": ("end_price", "End Price"),
        "limit": ("limit_price", "Limit Price"),
    }

    field, label = price_map.get(price_type, ("start_price", "Start Price"))
    current = config.get(field, 0)

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = field

    keyboard = [
        [InlineKeyboardButton(text="✕ Cancel", callback_data="bots:gs_back_to_prices")]
    ]

    await query.message.edit_text(
        f"*Edit {escape_markdown_v2(label)}*" + "\n\n"
        f"Current: `{current:,.6g}`" + "\n\n"
        r"Enter new price:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def _trigger_gs_save(update: Update, context: Context) -> None:
    """Trigger save after ID edit"""
    message_id = context.user_data.get("gs_wizard_message_id")
    chat_id = context.user_data.get("gs_wizard_chat_id")

    if not message_id or not chat_id:
        return

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

    fake_update = type(
        "FakeUpdate",
        (),
        {"callback_query": FakeQuery(context.bot, chat_id, message_id)},
    )()
    await handle_gs_save(fake_update, context)
