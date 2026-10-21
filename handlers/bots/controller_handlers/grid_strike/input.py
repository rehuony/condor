"""Grid Strike wizard free-text input dispatcher."""

import asyncio
import logging

from condor.telegram.types import (
    Context,
    Update,
)
from handlers.cex._shared import (
    get_correct_pair_format,
    get_trading_rules,
    validate_trading_pair,
)
from utils import telegram_ui as ui
from utils.telegram_formatters import format_error_message

from ..._shared import (
    GRID_STRIKE_DEFAULTS,
    SIDE_LONG,
    SIDE_SHORT,
    get_bots_client,
    get_controller_config,
    set_controller_config,
)
from ..grid_strike.review import (
    _trigger_gs_save,
    _update_wizard_caption_only,
    _update_wizard_message_for_prices_after_edit,
    _update_wizard_message_for_review,
)
from ..grid_strike.steps import (
    _background_fetch_market_data,
    _calculate_min_order_amount,
    _show_gs_pair_suggestions,
    _update_wizard_message_for_amount,
    _update_wizard_message_for_prices,
    _update_wizard_message_for_side,
)

logger = logging.getLogger(__name__)


async def process_gs_wizard_input(
    update: Update, context: Context, user_input: str
) -> None:
    """Process text input during wizard flow"""
    step = context.user_data.get("gs_wizard_step")
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    logger.debug(f"GS wizard input: step={step}, input={user_input[:50]}")

    if not step:
        logger.warning("GS wizard input called but no step set")
        return

    try:
        # Delete user's message
        try:
            await update.message.delete()
        except:
            pass

        if step == "trading_pair":
            # Validate and set trading pair
            pair = user_input.upper().strip()
            if "-" not in pair:
                pair = pair.replace("/", "-").replace("_", "-")

            connector = config.get("connector_name", "")

            # Validate trading pair exists on the connector
            client, _ = await get_bots_client(chat_id, context.user_data)
            is_valid, error_msg, suggestions, correct_pair = (
                await validate_trading_pair(context.user_data, client, connector, pair)
            )

            if not is_valid:
                # Show error with suggestions
                await _show_gs_pair_suggestions(
                    update, context, pair, error_msg, suggestions, connector
                )
                return

            # Use the correct pair format returned by validation
            if correct_pair:
                pair = correct_pair
            else:
                # Fallback: Get correctly formatted pair from trading rules
                trading_rules = await get_trading_rules(
                    context.user_data, client, connector
                )
                fallback_pair = get_correct_pair_format(trading_rules, pair)
                if fallback_pair:
                    pair = fallback_pair

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

            config["trading_pair"] = pair
            set_controller_config(context, config)

            # Start background fetch of market data
            asyncio.create_task(_background_fetch_market_data(context, config, chat_id))

            # Move to side step
            context.user_data["gs_wizard_step"] = "side"

            # Update the wizard message
            await _update_wizard_message_for_side(update, context)

        elif step == "prices":
            # Handle multiple input formats:
            # 1. field=value - set any field (e.g., start_price=130, order_frequency=5)
            # 2. start,end,limit - price values (legacy)
            # 3. tp:0.1 - take profit percentage (legacy)
            # 4. spread:0.05 - min spread percentage (legacy)
            # 5. min:10 - min order amount (legacy)
            input_stripped = user_input.strip()
            input_lower = input_stripped.lower()

            # Check for field=value format first
            if "=" in input_stripped:
                # Parse field=value format
                changes_made = False
                chart_affecting_change = False  # Track if chart needs regeneration
                warning_msg = None
                # Fields that affect the chart visualization
                chart_fields = {
                    "start_price",
                    "start",
                    "end_price",
                    "end",
                    "limit_price",
                    "limit",
                    "connector_name",
                    "trading_pair",
                }

                for line in input_stripped.split("\n"):
                    line = line.strip()
                    if not line or "=" not in line:
                        continue

                    field, value = line.split("=", 1)
                    field = field.strip().lower()
                    value = value.strip()

                    # Check if this field affects the chart
                    if field in chart_fields:
                        chart_affecting_change = True

                    # Map field names and set values
                    if field in ("start_price", "start"):
                        config["start_price"] = float(value)
                        changes_made = True
                    elif field in ("end_price", "end"):
                        config["end_price"] = float(value)
                        changes_made = True
                    elif field in ("limit_price", "limit"):
                        config["limit_price"] = float(value)
                        changes_made = True
                    elif field in ("take_profit", "tp"):
                        # Support both decimal (0.001) and percentage (0.1%)
                        val = float(value.replace("%", ""))
                        if val > 1:  # Likely percentage like 0.1
                            val = val / 100
                        config.setdefault(
                            "triple_barrier_config",
                            GRID_STRIKE_DEFAULTS["triple_barrier_config"].copy(),
                        )
                        config["triple_barrier_config"]["take_profit"] = val
                        changes_made = True
                    elif field in ("min_spread_between_orders", "min_spread", "spread"):
                        val = float(value.replace("%", ""))
                        if val > 1:  # Likely percentage
                            val = val / 100
                        config["min_spread_between_orders"] = val
                        changes_made = True
                    elif field in (
                        "min_order_amount_quote",
                        "min_order_amount",
                        "min_order",
                        "min",
                    ):
                        new_min_amt = float(value.replace("$", ""))
                        # Validate against trading rules
                        current_price = context.user_data.get("gs_current_price", 0)
                        trading_rules = context.user_data.get("gs_trading_rules", {})
                        required_min = _calculate_min_order_amount(
                            current_price, trading_rules, default=6.0
                        )
                        if new_min_amt < required_min:
                            config["min_order_amount_quote"] = required_min
                            warning_msg = f"Min order must be >= ${required_min:.2f}"
                        else:
                            config["min_order_amount_quote"] = new_min_amt
                        changes_made = True
                    elif field in ("total_amount_quote", "total_amount", "amount"):
                        config["total_amount_quote"] = float(value)
                        changes_made = True
                    elif field == "leverage":
                        config["leverage"] = int(float(value))
                        changes_made = True
                    elif field == "side":
                        config["side"] = int(float(value))
                        changes_made = True
                    elif field in ("max_open_orders", "max_orders"):
                        config["max_open_orders"] = int(float(value))
                        changes_made = True
                    elif field == "order_frequency":
                        config["order_frequency"] = int(float(value))
                        changes_made = True
                    elif field == "max_orders_per_batch":
                        config["max_orders_per_batch"] = int(float(value))
                        changes_made = True
                    elif field == "activation_bounds":
                        val = float(value.replace("%", ""))
                        if val > 1:  # Likely percentage
                            val = val / 100
                        config["activation_bounds"] = val
                        changes_made = True
                    elif field in ("coerce_tp_to_step", "coerce_tp", "coerce"):
                        # Boolean field - accept true/false/1/0/yes/no
                        val_lower = value.lower()
                        config["coerce_tp_to_step"] = val_lower in (
                            "true",
                            "1",
                            "yes",
                            "on",
                        )
                        changes_made = True
                    elif field == "position_mode":
                        config["position_mode"] = value.upper()
                        changes_made = True

                if changes_made:
                    set_controller_config(context, config)
                    # Only regenerate chart if price/pair fields changed
                    if chart_affecting_change:
                        await _update_wizard_message_for_prices_after_edit(
                            update, context
                        )
                    else:
                        await _update_wizard_caption_only(
                            update, context, warning_msg=warning_msg
                        )
                else:
                    raise ValueError(f"Unknown field: {field}")

            elif input_lower.startswith("tp:"):
                # Take profit in percentage (e.g., tp:0.1 = 0.1% = 0.001)
                tp_pct = float(input_lower.replace("tp:", "").replace("%", "").strip())
                tp_decimal = tp_pct / 100
                if "triple_barrier_config" not in config:
                    config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS[
                        "triple_barrier_config"
                    ].copy()
                config["triple_barrier_config"]["take_profit"] = tp_decimal
                set_controller_config(context, config)
                await _update_wizard_caption_only(update, context)

            elif input_lower.startswith("spread:"):
                # Min spread in percentage (e.g., spread:0.05 = 0.05% = 0.0005)
                spread_pct = float(
                    input_lower.replace("spread:", "").replace("%", "").strip()
                )
                spread_decimal = spread_pct / 100
                config["min_spread_between_orders"] = spread_decimal
                set_controller_config(context, config)
                await _update_wizard_caption_only(update, context)

            elif input_lower.startswith("min:"):
                # Min order amount in quote (e.g., min:10 = $10)
                min_amt = float(
                    input_lower.replace("min:", "").replace("$", "").strip()
                )
                # Validate against trading rules
                current_price = context.user_data.get("gs_current_price", 0)
                trading_rules = context.user_data.get("gs_trading_rules", {})
                required_min = _calculate_min_order_amount(
                    current_price, trading_rules, default=6.0
                )
                warning_msg = None
                if min_amt < required_min:
                    config["min_order_amount_quote"] = required_min
                    warning_msg = f"Min order must be >= ${required_min:.2f}"
                else:
                    config["min_order_amount_quote"] = min_amt
                set_controller_config(context, config)
                await _update_wizard_caption_only(
                    update, context, warning_msg=warning_msg
                )

            else:
                # Parse comma-separated prices: start,end,limit
                parts = user_input.replace(" ", "").split(",")
                if len(parts) == 3:
                    config["start_price"] = float(parts[0])
                    config["end_price"] = float(parts[1])
                    config["limit_price"] = float(parts[2])
                    set_controller_config(context, config)
                    # Stay in prices step to show updated values
                    await _update_wizard_message_for_prices_after_edit(update, context)
                elif len(parts) == 1:
                    # Single price - ask which one to update
                    raise ValueError("Use format: field=value (e.g., start_price=130)")
                else:
                    raise ValueError("Invalid format")

        elif step == "take_profit":
            # Parse take profit - interpret as percentage (0.4 = 0.4% = 0.004)
            tp_input = user_input.replace("%", "").strip()
            tp_pct = float(tp_input)
            tp_decimal = tp_pct / 100  # Convert 0.4 -> 0.004

            config = get_controller_config(context)
            if "triple_barrier_config" not in config:
                config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS[
                    "triple_barrier_config"
                ].copy()
            config["triple_barrier_config"]["take_profit"] = tp_decimal
            set_controller_config(context, config)

            # Move to review step
            context.user_data["gs_wizard_step"] = "review"
            await _update_wizard_message_for_review(update, context)

        elif step == "total_amount_quote":
            amount = float(user_input)
            config["total_amount_quote"] = amount
            set_controller_config(context, config)

            # Move to prices step
            context.user_data["gs_wizard_step"] = "prices"
            await _update_wizard_message_for_prices(update, context)

        elif step == "leverage":
            # Parse leverage - handle formats like "2", "2x", "2X", "10x"
            lev_input = user_input.strip().lower().replace("x", "")
            leverage = int(float(lev_input))

            if leverage < 1:
                raise ValueError("Leverage must be at least 1")

            config["leverage"] = leverage
            set_controller_config(context, config)

            # Move to amount step
            context.user_data["gs_wizard_step"] = "total_amount_quote"
            await _update_wizard_message_for_amount(update, context)

        elif step == "edit_id":
            new_id = user_input.strip()
            config["id"] = new_id
            set_controller_config(context, config)

            # Save immediately
            context.user_data["gs_wizard_step"] = "review"
            await _trigger_gs_save(update, context)

        elif step in ["start_price", "end_price", "limit_price"]:
            price = float(user_input)
            price_field = step.replace("_price", "_price")
            config[step] = price
            set_controller_config(context, config)

            # Go back to prices step
            context.user_data["gs_wizard_step"] = "prices"
            await _update_wizard_message_for_prices_after_edit(update, context)

        elif step == "edit_tp":
            tp_input = user_input.replace("%", "").strip()
            tp_pct = float(tp_input)
            tp_decimal = tp_pct / 100  # Convert 0.03 -> 0.0003
            if "triple_barrier_config" not in config:
                config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS[
                    "triple_barrier_config"
                ].copy()
            config["triple_barrier_config"]["take_profit"] = tp_decimal
            set_controller_config(context, config)
            context.user_data["gs_wizard_step"] = "review"
            await _update_wizard_message_for_review(update, context)

        elif step == "edit_act":
            act_input = user_input.replace("%", "").strip()
            act_pct = float(act_input)
            act_decimal = act_pct / 100  # Convert 1 -> 0.01
            config["activation_bounds"] = act_decimal
            set_controller_config(context, config)
            context.user_data["gs_wizard_step"] = "review"
            await _update_wizard_message_for_review(update, context)

        elif step == "edit_max_orders":
            config["max_open_orders"] = int(user_input)
            set_controller_config(context, config)
            context.user_data["gs_wizard_step"] = "review"
            await _update_wizard_message_for_review(update, context)

        elif step == "edit_batch":
            config["max_orders_per_batch"] = int(user_input)
            set_controller_config(context, config)
            context.user_data["gs_wizard_step"] = "review"
            await _update_wizard_message_for_review(update, context)

        elif step == "edit_min_amt":
            new_min_amt = float(user_input)
            # Validate against trading rules
            current_price = context.user_data.get("gs_current_price", 0)
            trading_rules = context.user_data.get("gs_trading_rules", {})
            required_min = _calculate_min_order_amount(
                current_price, trading_rules, default=6.0
            )
            if new_min_amt < required_min:
                config["min_order_amount_quote"] = required_min
                # Send warning message
                warn_msg = await update.message.answer(
                    ui.notice(
                        "warning",
                        "Minimum order adjusted",
                        f"The minimum order is ${required_min:.2f}, so it was set to that.",
                    ),
                    parse_mode="MarkdownV2",
                )
                await asyncio.sleep(3)
                try:
                    await warn_msg.delete()
                except:
                    pass
            else:
                config["min_order_amount_quote"] = new_min_amt
            set_controller_config(context, config)
            context.user_data["gs_wizard_step"] = "review"
            await _update_wizard_message_for_review(update, context)

        elif step == "edit_spread":
            config["min_spread_between_orders"] = float(user_input)
            set_controller_config(context, config)
            context.user_data["gs_wizard_step"] = "review"
            await _update_wizard_message_for_review(update, context)

        elif step == "review":
            # Parse field: value or field=value pairs (YAML-style)
            field_map = {
                # Real YAML field names
                "id": "id",
                "connector_name": "connector_name",
                "trading_pair": "trading_pair",
                "side": "side",
                "leverage": "leverage",
                "position_mode": "position_mode",
                "total_amount_quote": "total_amount_quote",
                "start_price": "start_price",
                "end_price": "end_price",
                "limit_price": "limit_price",
                "take_profit": "triple_barrier_config.take_profit",
                "open_order_type": "triple_barrier_config.open_order_type",
                "take_profit_order_type": "triple_barrier_config.take_profit_order_type",
                "coerce_tp_to_step": "coerce_tp_to_step",
                "keep_position": "keep_position",
                "activation_bounds": "activation_bounds",
                "max_open_orders": "max_open_orders",
                "max_orders_per_batch": "max_orders_per_batch",
                "order_frequency": "order_frequency",
                "min_order_amount_quote": "min_order_amount_quote",
                "min_spread_between_orders": "min_spread_between_orders",
            }

            updated_fields = []
            lines = user_input.strip().split("\n")
            for line in lines:
                line = line.strip()
                # Support both YAML style (field: value) and equals style (field=value)
                if ":" in line:
                    key, value = line.split(":", 1)
                elif "=" in line:
                    key, value = line.split("=", 1)
                else:
                    continue
                key = key.strip().lower()
                value = value.strip()

                if key not in field_map:
                    continue

                field = field_map[key]

                # Handle special cases
                if key == "side":
                    # Accept both numeric (1, 2) and text (LONG, SHORT)
                    if value in ("1", "LONG", "long"):
                        config["side"] = SIDE_LONG
                    else:
                        config["side"] = SIDE_SHORT
                elif key == "position_mode":
                    # Accept HEDGE or ONEWAY (case insensitive)
                    config["position_mode"] = (
                        "ONEWAY" if value.upper() == "ONEWAY" else "HEDGE"
                    )
                elif key == "keep_position":
                    config["keep_position"] = value.lower() in ("true", "yes", "y", "1")
                elif key == "coerce_tp_to_step":
                    config["coerce_tp_to_step"] = value.lower() in (
                        "true",
                        "yes",
                        "y",
                        "1",
                    )
                elif key == "take_profit":
                    if "triple_barrier_config" not in config:
                        config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS[
                            "triple_barrier_config"
                        ].copy()
                    config["triple_barrier_config"]["take_profit"] = float(value)
                elif key == "open_order_type":
                    if "triple_barrier_config" not in config:
                        config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS[
                            "triple_barrier_config"
                        ].copy()
                    config["triple_barrier_config"]["open_order_type"] = int(value)
                elif key == "take_profit_order_type":
                    if "triple_barrier_config" not in config:
                        config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS[
                            "triple_barrier_config"
                        ].copy()
                    config["triple_barrier_config"]["take_profit_order_type"] = int(
                        value
                    )
                elif field in [
                    "leverage",
                    "max_open_orders",
                    "max_orders_per_batch",
                    "order_frequency",
                ]:
                    config[field] = int(value)
                elif field in [
                    "total_amount_quote",
                    "start_price",
                    "end_price",
                    "limit_price",
                    "activation_bounds",
                    "min_order_amount_quote",
                    "min_spread_between_orders",
                ]:
                    config[field] = float(value)
                else:
                    config[field] = value

                updated_fields.append(key)

            if updated_fields:
                set_controller_config(context, config)
                await _update_wizard_message_for_review(update, context)
            else:
                raise ValueError("No valid fields found")

    except ValueError as e:
        # Send error and let user try again
        logger.warning(f"GS wizard input ValueError: {e}")
        error_msg = await update.message.answer(
            ui.notice("error", "Invalid input", "Enter a valid value."),
            parse_mode="MarkdownV2",
        )
        # Auto-delete error after 3 seconds
        await asyncio.sleep(3)
        try:
            await error_msg.delete()
        except:
            pass
    except Exception as e:
        # Catch any other exceptions and log them
        logger.error(f"GS wizard input error: {e}", exc_info=True)
        try:
            error_msg = await update.message.answer(
                format_error_message(f"Could not process the input: {str(e)[:100]}"),
                parse_mode="MarkdownV2",
            )
            await asyncio.sleep(3)
            await error_msg.delete()
        except:
            pass
