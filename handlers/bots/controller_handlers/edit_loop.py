"""Edit loop: walk through selected configs and edit their fields in sequence."""

import copy
import logging

from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from utils.telegram_formatters import escape_markdown_v2

from .._shared import (
    GRID_STRIKE_DEFAULTS,
    GS_EDITABLE_FIELDS,
    SIDE_LONG,
    clean_config_for_save,
    get_bots_client,
    get_controller_config,
    set_controller_config,
)
from ..controllers.pmm_mister import FIELD_ORDER as PMM_FIELD_ORDER
from ..controllers.pmm_mister import FIELDS as PMM_FIELDS
from .configs_menu import (
    _run_batched,
    show_configs_by_type,
    show_controller_configs_menu,
)

logger = logging.getLogger(__name__)


async def handle_cfg_edit_loop(update: Update, context: Context) -> None:
    """Start editing selected configs in a loop"""
    query = update.callback_query
    selected = context.user_data.get("selected_configs", {})
    selected_ids = [cfg_id for cfg_id, is_sel in selected.items() if is_sel]
    all_configs = context.user_data.get("controller_configs_list", [])

    if not selected_ids:
        await query.answer("No configs selected", show_alert=True)
        return

    # Build list of configs to edit
    configs_to_edit = []
    for cfg_id in selected_ids:
        for cfg in all_configs:
            if cfg.get("id") == cfg_id:
                configs_to_edit.append(cfg.copy())
                break

    if not configs_to_edit:
        await query.answer("Configs not found", show_alert=True)
        return

    # Store edit loop state
    context.user_data["cfg_edit_loop"] = configs_to_edit
    context.user_data["cfg_edit_index"] = 0
    context.user_data["cfg_edit_modified"] = {}  # {config_id: modified_config}

    await show_cfg_edit_form(update, context)


def _get_editable_config_fields(config: dict) -> dict:
    """Extract editable fields from a controller config using centralized field definitions"""
    controller_type = config.get("controller_name", "grid_strike")
    tp_cfg = config.get("triple_barrier_config", {})
    take_profit = (
        tp_cfg.get("take_profit", 0.0001) if isinstance(tp_cfg, dict) else 0.0001
    )

    if "grid_strike" in controller_type:
        # Use centralized GS_EDITABLE_FIELDS for consistency between wizard and edit views
        result = {}
        for field_name in GS_EDITABLE_FIELDS:
            if field_name == "take_profit":
                result[field_name] = take_profit
            else:
                default_val = GRID_STRIKE_DEFAULTS.get(field_name, "")
                result[field_name] = config.get(field_name, default_val)
        return result
    elif "pmm" in controller_type:
        # Use centralized PMM_FIELDS and PMM_FIELD_ORDER for consistency
        # between config creation and editing
        from ..controllers.pmm_mister import DEFAULTS as PMM_DEFAULTS

        result = {}
        for field_name in PMM_FIELD_ORDER:
            # Skip 'id' - it's shown in the header already
            if field_name == "id":
                continue
            if field_name in PMM_FIELDS:
                # Get value from config, fallback to PMM_DEFAULTS
                default_val = PMM_DEFAULTS.get(field_name, "")
                result[field_name] = config.get(field_name, default_val)
        return result
    # Default fields for other controller types
    return {
        "total_amount_quote": config.get("total_amount_quote", 0),
        "take_profit": take_profit,
    }


async def show_cfg_edit_form(
    update: Update, context: Context, status_msg: str = None
) -> None:
    """Show edit form for current config in bulk edit format (key=value)"""
    query = update.callback_query

    configs_to_edit = context.user_data.get("cfg_edit_loop", [])
    current_idx = context.user_data.get("cfg_edit_index", 0)
    modified = context.user_data.get("cfg_edit_modified", {})

    if not configs_to_edit or current_idx >= len(configs_to_edit):
        await show_controller_configs_menu(update, context)
        return

    total = len(configs_to_edit)
    config = configs_to_edit[current_idx]
    config_id = config.get("id", "unknown")

    # Check if we have modifications for this config
    if config_id in modified:
        config = modified[config_id]

    # Store current config for editing
    set_controller_config(context, config)

    # Get editable fields
    editable_fields = _get_editable_config_fields(config)

    # Store editable fields and set state for bulk edit
    context.user_data["cfg_editable_fields"] = editable_fields
    context.user_data["bots_state"] = "cfg_bulk_edit"
    context.user_data["cfg_edit_message_id"] = (
        query.message.message_id if not query.message.photo else None
    )
    context.user_data["cfg_edit_chat_id"] = query.message.chat.id

    # Build message with key=value format
    header = f"*Edit Config* \\({current_idx + 1}/{total}\\)"
    if status_msg:
        header += f" — {escape_markdown_v2(status_msg)}"
    lines = [header, ""]
    lines.append(f"`{escape_markdown_v2(config_id)}`")
    lines.append("")

    # Add context info for Grid Strike (connector, trading pair, side)
    controller_type = config.get("controller_name", "")
    if "grid_strike" in controller_type:
        connector = config.get("connector_name", "")
        pair = config.get("trading_pair", "")
        side = config.get("side", SIDE_LONG)
        side_str = "LONG" if side == SIDE_LONG else "SHORT"
        lines.append(
            f"*{escape_markdown_v2(pair)}* {side_str} on {escape_markdown_v2(connector)}"
        )
        lines.append("")

    # Build config text for display (each line copyable)
    for key, value in editable_fields.items():
        lines.append(f"`{key}={value}`")
    lines.append("")
    lines.append("✏️ _Send `key=value` to update_")

    # Build keyboard - simplified, no field buttons
    keyboard = []

    # Navigation row
    nav_row = []
    if current_idx > 0:
        nav_row.append(
            InlineKeyboardButton(text="◀️ Prev", callback_data="bots:cfg_edit_prev")
        )
    nav_row.append(
        InlineKeyboardButton(text=f"💾 Save", callback_data="bots:cfg_edit_save")
    )
    if current_idx < total - 1:
        nav_row.append(
            InlineKeyboardButton(text="Next ▶️", callback_data="bots:cfg_edit_next")
        )
    keyboard.append(nav_row)

    # Branch button row
    keyboard.append(
        [
            InlineKeyboardButton(text="🔀 Branch", callback_data="bots:cfg_branch"),
        ]
    )

    # Final row
    keyboard.append(
        [
            InlineKeyboardButton(
                text="💾 Save All & Exit", callback_data="bots:cfg_edit_save_all"
            ),
            InlineKeyboardButton(
                text="❌ Cancel", callback_data="bots:cfg_edit_cancel"
            ),
        ]
    )

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await query.message.edit_text(
        "\n".join(lines), parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_cfg_edit_field(
    update: Update, context: Context, field_name: str
) -> None:
    """Prompt to edit a field in the current config"""
    query = update.callback_query
    config = get_controller_config(context)

    if not config:
        await query.answer("Config not found", show_alert=True)
        return

    # Get current value
    if field_name == "take_profit":
        current_value = config.get("triple_barrier_config", {}).get(
            "take_profit", 0.0001
        )
    elif field_name == "side":
        # Toggle side directly
        current_side = config.get("side", 1)
        new_side = 2 if current_side == 1 else 1
        config["side"] = new_side

        # Store modified config
        config_id = config.get("id")
        modified = context.user_data.get("cfg_edit_modified", {})
        modified[config_id] = config
        context.user_data["cfg_edit_modified"] = modified

        # Update in edit loop
        configs_to_edit = context.user_data.get("cfg_edit_loop", [])
        current_idx = context.user_data.get("cfg_edit_index", 0)
        if current_idx < len(configs_to_edit):
            configs_to_edit[current_idx] = config

        await show_cfg_edit_form(update, context)
        return
    else:
        current_value = config.get(field_name, "")

    # Get field info
    field_labels = {
        "leverage": ("Leverage", "Enter leverage (1-20)"),
        "total_amount_quote": ("Amount (USDT)", "Enter total amount in quote currency"),
        "start_price": ("Start Price", "Enter start price"),
        "end_price": ("End Price", "Enter end price"),
        "limit_price": ("Limit Price", "Enter limit/stop price"),
        "take_profit": ("Take Profit", "Enter take profit (e.g., 0.01 = 1%)"),
        "max_open_orders": ("Max Open Orders", "Enter max open orders (1-10)"),
    }

    label, hint = field_labels.get(field_name, (field_name, "Enter value"))

    # Store state for input processing
    context.user_data["bots_state"] = f"cfg_edit_input:{field_name}"
    context.user_data["cfg_edit_field"] = field_name

    lines = [
        f"*Edit {escape_markdown_v2(label)}*",
        "",
        f"Current: `{escape_markdown_v2(str(current_value))}`",
        "",
        f"_{escape_markdown_v2(hint)}_",
    ]

    keyboard = [
        [InlineKeyboardButton(text="❌ Cancel", callback_data="bots:cfg_edit_form")]
    ]

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def process_cfg_edit_input(
    update: Update, context: Context, user_input: str
) -> None:
    """Process user input for config bulk edit - parses key=value lines"""
    chat_id = update.effective_chat.id
    config = get_controller_config(context)
    editable_fields = context.user_data.get("cfg_editable_fields", {})

    if not config:
        await update.message.answer("Context lost. Please start over.")
        return

    # Delete user's input message for clean chat
    try:
        await update.message.delete()
    except Exception:
        pass

    # Parse key=value lines
    updates = {}
    errors = []

    for line in user_input.split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # Validate key exists in editable fields
        if key not in editable_fields:
            errors.append(f"Unknown: {key}")
            continue

        # Convert value to appropriate type
        current_val = editable_fields.get(key)
        try:
            if isinstance(current_val, bool):
                parsed_value = value.lower() in ["true", "1", "yes", "y", "on"]
            elif isinstance(current_val, int):
                parsed_value = int(value)
            elif isinstance(current_val, float):
                parsed_value = float(value)
            else:
                parsed_value = value
            updates[key] = parsed_value
        except ValueError:
            errors.append(f"Invalid: {key}={value}")

    if errors:
        error_msg = "⚠️ " + ", ".join(errors)
        await context.bot.send_message(chat_id=chat_id, text=error_msg)

    if not updates:
        await context.bot.send_message(
            chat_id=chat_id, text="❌ No valid updates found. Use format: key=value"
        )
        return

    # Apply updates to config
    old_config_id = config.get("id", "")
    for key, value in updates.items():
        if key == "take_profit":
            if "triple_barrier_config" not in config:
                config["triple_barrier_config"] = {}
            config["triple_barrier_config"]["take_profit"] = value
        else:
            config[key] = value

    # Auto-update ID if connector_name or trading_pair changed
    if "connector_name" in updates or "trading_pair" in updates:
        # Extract sequence number from old ID
        parts = old_config_id.split("_", 1)
        seq_num = parts[0] if parts and parts[0].isdigit() else "001"

        # Determine controller type abbreviation
        controller_name = config.get("controller_name", "")
        if controller_name == "grid_strike":
            type_abbrev = "gs"
        elif controller_name == "pmm_mister":
            type_abbrev = "pmm"
        else:
            type_abbrev = (
                parts[1].split("_")[0] if len(parts) > 1 and "_" in parts[1] else "cfg"
            )

        # Build new ID with current values
        connector = config.get("connector_name", "unknown")
        conn_clean = connector.replace("_perpetual", "").replace("_spot", "")
        pair = config.get("trading_pair", "UNKNOWN").upper()
        new_config_id = f"{seq_num}_{type_abbrev}_{conn_clean}_{pair}"

        config["id"] = new_config_id
    else:
        new_config_id = old_config_id

    # Store modified config (remove old key if ID changed)
    modified = context.user_data.get("cfg_edit_modified", {})
    if old_config_id != new_config_id and old_config_id in modified:
        del modified[old_config_id]
    modified[new_config_id] = config
    context.user_data["cfg_edit_modified"] = modified

    # Update in edit loop
    configs_to_edit = context.user_data.get("cfg_edit_loop", [])
    current_idx = context.user_data.get("cfg_edit_index", 0)
    if current_idx < len(configs_to_edit):
        configs_to_edit[current_idx] = config

    # Update editable fields for display
    editable_fields = _get_editable_config_fields(config)
    context.user_data["cfg_editable_fields"] = editable_fields

    # Try to delete the user's input message
    try:
        await update.message.delete()
    except Exception:
        pass

    # Rebuild the edit form with updated values
    total = len(configs_to_edit)
    config_id = config.get("id", "unknown")

    lines = [f"*Edit Config* \\({current_idx + 1}/{total}\\)", ""]
    lines.append(f"`{escape_markdown_v2(config_id)}`")
    lines.append("")

    # Add context info for Grid Strike (connector, trading pair, side)
    controller_type = config.get("controller_name", "")
    if "grid_strike" in controller_type:
        connector = config.get("connector_name", "")
        pair = config.get("trading_pair", "")
        side = config.get("side", SIDE_LONG)
        side_str = "LONG" if side == SIDE_LONG else "SHORT"
        lines.append(
            f"*{escape_markdown_v2(pair)}* {side_str} on {escape_markdown_v2(connector)}"
        )
        lines.append("")

    for key, value in editable_fields.items():
        lines.append(f"`{key}={value}`")
    lines.append("")
    lines.append("✏️ _Send `key=value` to update_")

    # Build keyboard
    keyboard = []
    nav_row = []
    if current_idx > 0:
        nav_row.append(
            InlineKeyboardButton(text="◀️ Prev", callback_data="bots:cfg_edit_prev")
        )
    nav_row.append(
        InlineKeyboardButton(text=f"💾 Save", callback_data="bots:cfg_edit_save")
    )
    if current_idx < total - 1:
        nav_row.append(
            InlineKeyboardButton(text="Next ▶️", callback_data="bots:cfg_edit_next")
        )
    keyboard.append(nav_row)
    keyboard.append(
        [InlineKeyboardButton(text="🔀 Branch", callback_data="bots:cfg_branch")]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                text="💾 Save All & Exit", callback_data="bots:cfg_edit_save_all"
            ),
            InlineKeyboardButton(
                text="❌ Cancel", callback_data="bots:cfg_edit_cancel"
            ),
        ]
    )

    # Edit the original message
    message_id = context.user_data.get("cfg_edit_message_id")
    if message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="\n".join(lines),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )
        except Exception:
            # If edit fails, send a new message
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text="\n".join(lines),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )
            context.user_data["cfg_edit_message_id"] = msg.message_id


async def handle_cfg_edit_prev(update: Update, context: Context) -> None:
    """Go to previous config in edit loop"""
    current_idx = context.user_data.get("cfg_edit_index", 0)
    if current_idx > 0:
        context.user_data["cfg_edit_index"] = current_idx - 1
    await show_cfg_edit_form(update, context)


async def handle_cfg_edit_next(update: Update, context: Context) -> None:
    """Go to next config in edit loop"""
    configs_to_edit = context.user_data.get("cfg_edit_loop", [])
    current_idx = context.user_data.get("cfg_edit_index", 0)
    if current_idx < len(configs_to_edit) - 1:
        context.user_data["cfg_edit_index"] = current_idx + 1
    await show_cfg_edit_form(update, context)


async def handle_cfg_edit_save(update: Update, context: Context) -> None:
    """Save current config and stay in edit loop"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    config = get_controller_config(context)
    if not config:
        await query.answer("Config not found", show_alert=True)
        return

    config_id = config.get("id")

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        await client.controllers.create_or_update_controller_config(
            config_id, clean_config_for_save(config)
        )
        await query.answer()

        # Remove from modified since it's now saved
        modified = context.user_data.get("cfg_edit_modified", {})
        modified.pop(config_id, None)
        context.user_data["cfg_edit_modified"] = modified

        # Refresh form with saved status
        await show_cfg_edit_form(update, context, status_msg="✅ Saved!")

    except Exception as e:
        logger.error(f"Failed to save config {config_id}: {e}")
        await query.answer(f"❌ Save failed: {str(e)[:30]}", show_alert=True)


async def handle_cfg_edit_save_all(update: Update, context: Context) -> None:
    """Save all modified configs and exit edit loop"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    modified = context.user_data.get("cfg_edit_modified", {})

    if not modified:
        await query.answer("No changes to save")
        # Clean up edit loop state
        context.user_data.pop("cfg_edit_loop", None)
        context.user_data.pop("cfg_edit_index", None)
        context.user_data.pop("cfg_edit_modified", None)
        await show_controller_configs_menu(update, context)
        return

    # Show progress
    await query.message.edit_text(
        f"💾 Saving {len(modified)} config{'s' if len(modified) != 1 else ''}\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    client, _ = await get_bots_client(chat_id, context.user_data)
    saved, failed = await _run_batched(
        list(modified),
        lambda config_id: client.controllers.create_or_update_controller_config(
            config_id, clean_config_for_save(modified[config_id])
        ),
        verb="save",
    )

    # Clean up edit loop state
    context.user_data.pop("cfg_edit_loop", None)
    context.user_data.pop("cfg_edit_index", None)
    context.user_data.pop("cfg_edit_modified", None)

    # Build result message
    lines = []
    if saved:
        lines.append(f"✅ *Saved {len(saved)} config{'s' if len(saved) != 1 else ''}*")
        for cfg_id in saved[:5]:
            lines.append(f"  • `{escape_markdown_v2(cfg_id)}`")
        if len(saved) > 5:
            lines.append(f"  _\\.\\.\\.and {len(saved) - 5} more_")

    if failed:
        lines.append("")
        lines.append(f"❌ *Failed to save {len(failed)}:*")
        for cfg_id, error in failed[:3]:
            lines.append(f"  • `{escape_markdown_v2(cfg_id)}`")

    keyboard = [
        [InlineKeyboardButton(text="⬅️ Back", callback_data="bots:controller_configs")]
    ]

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_cfg_edit_cancel(update: Update, context: Context) -> None:
    """Cancel edit loop without saving"""
    # Clean up edit loop state
    context.user_data.pop("cfg_edit_loop", None)
    context.user_data.pop("cfg_edit_index", None)
    context.user_data.pop("cfg_edit_modified", None)
    context.user_data.pop("bots_state", None)

    await show_controller_configs_menu(update, context)


async def handle_cfg_branch(update: Update, context: Context) -> None:
    """Branch (duplicate) the current config with a new ID"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    configs_to_edit = context.user_data.get("cfg_edit_loop", [])
    current_idx = context.user_data.get("cfg_edit_index", 0)
    modified = context.user_data.get("cfg_edit_modified", {})

    if not configs_to_edit or current_idx >= len(configs_to_edit):
        await query.answer("No config to branch")
        return

    # Get current config (use modified version if exists)
    config = configs_to_edit[current_idx]
    config_id = config.get("id", "unknown")
    if config_id in modified:
        config = modified[config_id]

    # Generate new ID by incrementing the sequence number
    # Format: NNN_type_connector_pair -> increment NNN
    old_id = config.get("id", "")
    parts = old_id.split("_", 1)

    # Find highest sequence number across all configs from multiple sources
    client, _ = await get_bots_client(chat_id, context.user_data)

    # Source 1: Fresh list from API
    try:
        api_configs = await client.controllers.list_controller_configs()
    except Exception:
        api_configs = []

    # Source 2: Cached list in user_data (may have configs not yet saved)
    cached_configs = context.user_data.get("controller_configs_list", [])

    max_num = 0

    # Check all sources for highest sequence number
    all_config_sources = [api_configs, cached_configs, configs_to_edit]
    for config_list in all_config_sources:
        for cfg in config_list:
            cfg_id = cfg.get("id", "") if isinstance(cfg, dict) else ""
            cfg_parts = cfg_id.split("_", 1)
            if cfg_parts and cfg_parts[0].isdigit():
                max_num = max(max_num, int(cfg_parts[0]))

    # Also check modified config IDs (keys)
    for cfg_id in modified.keys():
        cfg_parts = cfg_id.split("_", 1)
        if cfg_parts and cfg_parts[0].isdigit():
            max_num = max(max_num, int(cfg_parts[0]))

    # Create new ID based on current config values
    new_num = str(max_num + 1).zfill(3)

    # Determine controller type abbreviation
    controller_name = config.get("controller_name", "")
    if controller_name == "grid_strike":
        type_abbrev = "gs"
    elif controller_name == "pmm_mister":
        type_abbrev = "pmm"
    else:
        # Fallback: try to extract from old ID
        if len(parts) > 1:
            type_abbrev = parts[1].split("_")[0] if "_" in parts[1] else parts[1]
        else:
            type_abbrev = "cfg"

    # Get connector and trading pair from current config values
    connector = config.get("connector_name", "unknown")
    conn_clean = connector.replace("_perpetual", "").replace("_spot", "")
    pair = config.get("trading_pair", "UNKNOWN").upper()

    new_id = f"{new_num}_{type_abbrev}_{conn_clean}_{pair}"

    # Deep copy the config with new ID
    new_config = copy.deepcopy(config)
    new_config["id"] = new_id

    # Add to edit loop right after current config
    configs_to_edit.insert(current_idx + 1, new_config)
    context.user_data["cfg_edit_loop"] = configs_to_edit

    # Mark as modified so it gets saved
    modified[new_id] = new_config
    context.user_data["cfg_edit_modified"] = modified

    # Navigate to the new config
    context.user_data["cfg_edit_index"] = current_idx + 1

    await query.answer(f"Branched to {new_id}")
    await show_cfg_edit_form(update, context)


async def handle_configs_page(update: Update, context: Context, page: int) -> None:
    """Handle pagination for controller configs menu (legacy, redirects to cfg_page)"""
    controller_type = context.user_data.get("configs_controller_type")
    if controller_type:
        await show_configs_by_type(update, context, controller_type, page)
    else:
        await show_controller_configs_menu(update, context, page=page)
