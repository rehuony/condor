"""Deploy controllers: selection menu and the progressive configuration flow."""

import logging

from condor.telegram.errors import BadRequest, is_not_modified
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from handlers._deeds import record_telegram_deed
from utils import telegram_ui as ui
from utils.telegram_formatters import escape_markdown_v2, format_error_message

from .._shared import (
    get_bots_client,
)
from .deploy_streamlined import show_deploy_config_step

logger = logging.getLogger(__name__)


# Default deploy settings
DEPLOY_DEFAULTS = {
    "instance_name": "",
    "credentials_profile": "master_account",
    "controllers_config": [],
    "max_global_drawdown_quote": None,
    "max_controller_drawdown_quote": None,
    "image": "hummingbot/hummingbot:latest",
}


# Deploy field configuration for progressive flow
DEPLOY_FIELDS = {
    "instance_name": {
        "label": "Instance Name",
        "required": True,
        "hint": "Name for your bot instance (e.g. my_grid_bot)",
        "type": "str",
        "default": None,
    },
    "credentials_profile": {
        "label": "Credentials Profile",
        "required": True,
        "hint": "Account profile with exchange credentials",
        "type": "str",
        "default": "master_account",
    },
    "max_global_drawdown_quote": {
        "label": "Max Global Drawdown",
        "required": False,
        "hint": "Maximum total loss in quote currency (e.g. 1000 USDT)",
        "type": "float",
        "default": None,
    },
    "max_controller_drawdown_quote": {
        "label": "Max Controller Drawdown",
        "required": False,
        "hint": "Maximum loss per controller in quote currency",
        "type": "float",
        "default": None,
    },
    "image": {
        "label": "Docker Image",
        "required": False,
        "hint": "Hummingbot image to use",
        "type": "str",
        "default": "hummingbot/hummingbot:latest",
    },
}


# Field order for progressive flow
DEPLOY_FIELD_ORDER = [
    "instance_name",
    "credentials_profile",
    "max_global_drawdown_quote",
    "max_controller_drawdown_quote",
    "image",
]


async def show_deploy_menu(update: Update, context: Context) -> None:
    """Show the deploy controllers menu"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.list_controller_configs()

        if not configs:
            keyboard = [
                [InlineKeyboardButton(text="« Back", callback_data="bots:main_menu")]
            ]
            await query.message.edit_text(
                r"*Deploy Controllers*" + "\n\n"
                r"No configurations available to deploy\." + "\n"
                r"Create a controller config first\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )
            return

        # Store configs and initialize selection
        context.user_data["controller_configs_list"] = configs
        selected = context.user_data.get("selected_controllers", set())

        # Build message
        lines = [r"*Deploy Controllers*", ""]
        lines.append(r"Select controllers to deploy:")
        lines.append("")

        # Build keyboard with checkboxes
        keyboard = []

        for i, config in enumerate(configs):
            config_id = config.get("id", config.get("config_name", f"config_{i}"))
            is_selected = i in selected
            checkbox = "[x]" if is_selected else "[ ]"

            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=f"{checkbox} {config_id[:25]}",
                        callback_data=f"bots:toggle_deploy:{i}",
                    )
                ]
            )

        # Action buttons
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="Select All", callback_data="bots:select_all"
                ),
                InlineKeyboardButton(text="Clear All", callback_data="bots:clear_all"),
            ]
        )

        if selected:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=f"Next: Configure ({len(selected)})",
                        callback_data="bots:deploy_configure",
                    ),
                ]
            )

        keyboard.append(
            [
                InlineKeyboardButton(text="« Back", callback_data="bots:main_menu"),
            ]
        )

        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        try:
            await query.message.edit_text(
                "\n".join(lines), parse_mode="MarkdownV2", reply_markup=reply_markup
            )
        except BadRequest as e:
            if not is_not_modified(e):
                raise

    except Exception as e:
        logger.error(f"Error loading deploy menu: {e}", exc_info=True)
        error_msg = format_error_message(f"Failed to load configs: {str(e)}")
        keyboard = [
            [InlineKeyboardButton(text="« Back", callback_data="bots:main_menu")]
        ]
        await query.message.edit_text(
            error_msg,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )


async def handle_toggle_deploy_selection(
    update: Update, context: Context, index: int
) -> None:
    """Toggle selection of a controller for deployment"""
    selected = context.user_data.get("selected_controllers", set())

    if index in selected:
        selected.discard(index)
    else:
        selected.add(index)

    context.user_data["selected_controllers"] = selected
    await show_deploy_menu(update, context)


async def handle_select_all(update: Update, context: Context) -> None:
    """Select all controllers for deployment"""
    configs = context.user_data.get("controller_configs_list", [])
    context.user_data["selected_controllers"] = set(range(len(configs)))
    await show_deploy_menu(update, context)


async def handle_clear_all(update: Update, context: Context) -> None:
    """Clear all selections"""
    context.user_data["selected_controllers"] = set()
    await show_deploy_menu(update, context)


async def show_deploy_configure(update: Update, context: Context) -> None:
    """Start the streamlined deployment configuration flow"""
    # Use the new streamlined deploy flow
    await show_deploy_config_step(update, context)


async def show_deploy_form(update: Update, context: Context) -> None:
    """Show the deployment configuration form with current values"""
    query = update.callback_query
    deploy_params = context.user_data.get("deploy_params", DEPLOY_DEFAULTS.copy())

    # Build display
    lines = [r"*Deploy Configuration*", ""]

    instance = deploy_params.get("instance_name", "") or "Not set"
    creds = deploy_params.get("credentials_profile", "") or "Not set"
    controllers = deploy_params.get("controllers_config", [])
    controllers_str = ", ".join(controllers) if controllers else "None"
    max_global = deploy_params.get("max_global_drawdown_quote")
    max_controller = deploy_params.get("max_controller_drawdown_quote")
    image = deploy_params.get("image", "hummingbot/hummingbot:latest")

    lines.append(f"*Instance Name*\\*: `{escape_markdown_v2(instance)}`")
    lines.append(f"*Credentials Profile*\\*: `{escape_markdown_v2(creds)}`")
    lines.append(f"*Controllers*: `{escape_markdown_v2(controllers_str[:50])}`")
    lines.append(f"*Max Global DD*: `{max_global if max_global else 'Not set'}`")
    lines.append(
        f"*Max Controller DD*: `{max_controller if max_controller else 'Not set'}`"
    )
    lines.append(f"*Image*: `{escape_markdown_v2(image)}`")
    lines.append("")
    lines.append(r"_\* \= required_")

    # Build keyboard
    keyboard = [
        [
            InlineKeyboardButton(
                text="Instance Name", callback_data="bots:deploy_set:instance_name"
            ),
            InlineKeyboardButton(
                text="Credentials", callback_data="bots:deploy_set:credentials_profile"
            ),
        ],
        [
            InlineKeyboardButton(
                text="Max Global DD",
                callback_data="bots:deploy_set:max_global_drawdown_quote",
            ),
            InlineKeyboardButton(
                text="Max Controller DD",
                callback_data="bots:deploy_set:max_controller_drawdown_quote",
            ),
        ],
        [
            InlineKeyboardButton(text="Image", callback_data="bots:deploy_set:image"),
        ],
    ]

    # Check if ready to deploy
    can_deploy = bool(
        deploy_params.get("instance_name") and deploy_params.get("credentials_profile")
    )

    if can_deploy:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="🚀 Deploy Now", callback_data="bots:execute_deploy"
                ),
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="« Back to Selection", callback_data="bots:deploy_menu"
            ),
        ]
    )

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await query.message.edit_text(
        "\n".join(lines), parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def show_deploy_progressive_form(update: Update, context: Context) -> None:
    """Show the progressive deployment configuration form"""
    query = update.callback_query

    deploy_params = context.user_data.get("deploy_params", DEPLOY_DEFAULTS.copy())
    current_field = context.user_data.get("deploy_current_field", DEPLOY_FIELD_ORDER[0])

    message_text, reply_markup = _build_deploy_progressive_message(
        deploy_params, current_field, context
    )

    await query.message.edit_text(
        message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )
    await query.answer()


def _build_deploy_progressive_message(
    deploy_params: dict, current_field: str, context
) -> tuple:
    """Build the progressive deploy configuration message."""
    controllers = deploy_params.get("controllers_config", [])
    controllers_str = ", ".join(controllers) if controllers else "None"

    lines = [r"*Deploy Configuration*", ""]
    lines.append(f"*Controllers:* `{escape_markdown_v2(controllers_str[:40])}`")
    lines.append("")

    for field_name in DEPLOY_FIELD_ORDER:
        field_info = DEPLOY_FIELDS[field_name]
        label = field_info["label"]
        required = "\\*" if field_info.get("required") else ""
        value = deploy_params.get(field_name)

        if value is not None and value != "":
            value_display = str(value)
            if field_name == "credentials_profile" and value == "master_account":
                value_display = "master_account (default)"
        else:
            default = field_info.get("default")
            value_display = f"{default} (default)" if default else "Not set"

        if field_name == current_field:
            lines.append(
                f"➡️ *{escape_markdown_v2(label)}*{required}: _awaiting input_"
            )
        elif DEPLOY_FIELD_ORDER.index(field_name) < DEPLOY_FIELD_ORDER.index(
            current_field
        ):
            lines.append(
                f"✅ *{escape_markdown_v2(label)}*{required}: `{escape_markdown_v2(value_display)}`"
            )
        else:
            lines.append(
                f"⬜ *{escape_markdown_v2(label)}*{required}: `{escape_markdown_v2(value_display)}`"
            )

    field_info = DEPLOY_FIELDS.get(current_field, {})
    hint = field_info.get("hint", "")
    if hint:
        lines.append("")
        lines.append(f"_Hint: {escape_markdown_v2(hint)}_")

    lines.append("")
    lines.append(r"_Type a value or use the buttons below\._")

    keyboard = []
    default_value = DEPLOY_FIELDS.get(current_field, {}).get("default")
    if default_value:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"✅ Use Default: {default_value[:20]}",
                    callback_data=f"bots:deploy_use_default:{current_field}",
                )
            ]
        )

    if not DEPLOY_FIELDS.get(current_field, {}).get("required"):
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="Skip (Keep Default)", callback_data="bots:deploy_skip_field"
                )
            ]
        )

    nav_buttons = []
    current_index = DEPLOY_FIELD_ORDER.index(current_field)
    if current_index > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="« Back", callback_data="bots:deploy_prev_field")
        )
    nav_buttons.append(
        InlineKeyboardButton(text="✕ Cancel", callback_data="bots:deploy_menu")
    )
    keyboard.append(nav_buttons)

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard)


async def handle_deploy_progressive_input(update: Update, context: Context) -> None:
    """Handle text input during progressive deploy configuration"""
    current_field = context.user_data.get("deploy_current_field")
    bots_state = context.user_data.get("bots_state")

    if bots_state != "deploy_progressive" or not current_field:
        return

    try:
        await update.message.delete()
    except:
        pass

    user_input = update.message.text.strip()
    field_info = DEPLOY_FIELDS.get(current_field, {})
    field_type = field_info.get("type", "str")

    try:
        if field_type == "float":
            value = float(user_input) if user_input else None
        elif field_type == "int":
            value = int(user_input) if user_input else None
        else:
            value = user_input

        deploy_params = context.user_data.get("deploy_params", DEPLOY_DEFAULTS.copy())
        deploy_params[current_field] = value
        context.user_data["deploy_params"] = deploy_params

        await _advance_deploy_field(update, context)

    except ValueError:
        import asyncio

        bot = context.bot
        chat_id = context.user_data.get("deploy_chat_id", update.effective_chat.id)
        error_msg = await bot.send_message(
            chat_id=chat_id,
            text=ui.notice("error", "Invalid value", f"Enter a valid {field_type}."),
            parse_mode="MarkdownV2",
        )
        await asyncio.sleep(3)
        try:
            await error_msg.delete()
        except:
            pass


async def _advance_deploy_field(update: Update, context) -> None:
    """Advance to the next deploy field or show summary"""
    current_field = context.user_data.get("deploy_current_field")
    current_index = DEPLOY_FIELD_ORDER.index(current_field)

    if current_index < len(DEPLOY_FIELD_ORDER) - 1:
        next_field = DEPLOY_FIELD_ORDER[current_index + 1]
        context.user_data["deploy_current_field"] = next_field
        await _update_deploy_progressive_message(context, context.bot)
    else:
        context.user_data["bots_state"] = "deploy_review"
        context.user_data.pop("deploy_current_field", None)
        await _show_deploy_summary(context, context.bot)


async def _update_deploy_progressive_message(context, bot) -> None:
    """Update the deploy progressive message with current progress"""
    message_id = context.user_data.get("deploy_message_id")
    chat_id = context.user_data.get("deploy_chat_id")
    current_field = context.user_data.get("deploy_current_field")
    deploy_params = context.user_data.get("deploy_params", {})

    if not message_id or not chat_id:
        return

    message_text, reply_markup = _build_deploy_progressive_message(
        deploy_params, current_field, context
    )

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup,
        )
    except Exception as e:
        logger.error(f"Error updating deploy message: {e}")


async def _show_deploy_summary(context, bot) -> None:
    """Show deployment summary before executing"""
    message_id = context.user_data.get("deploy_message_id")
    chat_id = context.user_data.get("deploy_chat_id")
    deploy_params = context.user_data.get("deploy_params", {})

    if not message_id or not chat_id:
        return

    controllers = deploy_params.get("controllers_config", [])
    controllers_str = ", ".join(controllers) if controllers else "None"

    lines = [r"*Deploy Configuration \- Review*", ""]
    lines.append(f"*Controllers:* `{escape_markdown_v2(controllers_str)}`")
    lines.append("")

    for field_name in DEPLOY_FIELD_ORDER:
        field_info = DEPLOY_FIELDS[field_name]
        label = field_info["label"]
        required = "\\*" if field_info.get("required") else ""
        value = deploy_params.get(field_name)

        if value is not None and value != "":
            value_display = str(value)
        else:
            default = field_info.get("default")
            if default:
                deploy_params[field_name] = default
                value_display = str(default)
            else:
                value_display = "Not set"

        lines.append(
            f"✅ *{escape_markdown_v2(label)}*{required}: `{escape_markdown_v2(value_display)}`"
        )

    context.user_data["deploy_params"] = deploy_params

    lines.append("")
    lines.append(r"_Ready to deploy\? Tap Deploy Now or edit any field\._")

    keyboard = []
    field_buttons = []
    for field_name in DEPLOY_FIELD_ORDER:
        label = DEPLOY_FIELDS[field_name]["label"]
        field_buttons.append(
            InlineKeyboardButton(
                text=f"✏️ {label[:15]}", callback_data=f"bots:deploy_edit:{field_name}"
            )
        )

    for i in range(0, len(field_buttons), 2):
        keyboard.append(field_buttons[i : i + 2])

    keyboard.append(
        [
            InlineKeyboardButton(
                text="🚀 Deploy Now", callback_data="bots:execute_deploy"
            )
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                text="« Back to Selection", callback_data="bots:deploy_menu"
            )
        ]
    )

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
    except Exception as e:
        logger.error(f"Error showing deploy summary: {e}")


async def handle_deploy_use_default(
    update: Update, context: Context, field_name: str
) -> None:
    """Use default value for a deploy field"""
    query = update.callback_query
    field_info = DEPLOY_FIELDS.get(field_name, {})
    default = field_info.get("default")

    if default:
        deploy_params = context.user_data.get("deploy_params", DEPLOY_DEFAULTS.copy())
        deploy_params[field_name] = default
        context.user_data["deploy_params"] = deploy_params

    await _advance_deploy_field(update, context)
    await query.answer()


async def handle_deploy_skip_field(update: Update, context: Context) -> None:
    """Skip the current optional deploy field"""
    query = update.callback_query
    current_field = context.user_data.get("deploy_current_field")
    field_info = DEPLOY_FIELDS.get(current_field, {})
    default = field_info.get("default")

    deploy_params = context.user_data.get("deploy_params", DEPLOY_DEFAULTS.copy())
    deploy_params[current_field] = default
    context.user_data["deploy_params"] = deploy_params

    await _advance_deploy_field(update, context)
    await query.answer()


async def handle_deploy_prev_field(update: Update, context: Context) -> None:
    """Go back to the previous deploy field"""
    query = update.callback_query
    current_field = context.user_data.get("deploy_current_field")
    current_index = DEPLOY_FIELD_ORDER.index(current_field)

    if current_index > 0:
        prev_field = DEPLOY_FIELD_ORDER[current_index - 1]
        context.user_data["deploy_current_field"] = prev_field
        await show_deploy_progressive_form(update, context)
    else:
        await query.answer("Already at first field")


async def handle_deploy_edit_field(
    update: Update, context: Context, field_name: str
) -> None:
    """Edit a specific field from the summary view"""
    query = update.callback_query
    context.user_data["deploy_current_field"] = field_name
    context.user_data["bots_state"] = "deploy_progressive"
    await show_deploy_progressive_form(update, context)


async def handle_deploy_set_field(
    update: Update, context: Context, field_name: str
) -> None:
    """Prompt user to enter a value for a deploy field"""
    query = update.callback_query

    labels = {
        "instance_name": "Instance Name",
        "credentials_profile": "Credentials Profile",
        "max_global_drawdown_quote": "Max Global Drawdown (Quote)",
        "max_controller_drawdown_quote": "Max Controller Drawdown (Quote)",
        "image": "Docker Image",
    }

    hints = {
        "instance_name": "e.g. my_grid_bot",
        "credentials_profile": "e.g. binance_main",
        "max_global_drawdown_quote": "e.g. 1000 (in USDT)",
        "max_controller_drawdown_quote": "e.g. 500 (in USDT)",
        "image": "e.g. hummingbot/hummingbot:latest",
    }

    label = labels.get(field_name, field_name)
    hint = hints.get(field_name, "")

    # Set state for text input
    context.user_data["bots_state"] = f"deploy_set:{field_name}"
    context.user_data["editing_deploy_field"] = field_name

    # Get current value
    deploy_params = context.user_data.get("deploy_params", {})
    current = deploy_params.get(field_name, "")
    current_str = str(current) if current else "Not set"

    message = (
        f"*Set {escape_markdown_v2(label)}*\n\n"
        f"Current: `{escape_markdown_v2(current_str)}`\n\n"
    )

    if hint:
        message += f"_Hint: {escape_markdown_v2(hint)}_\n\n"

    message += r"Type the new value or tap Cancel\."

    keyboard = [
        [InlineKeyboardButton(text="✕ Cancel", callback_data="bots:deploy_form_back")]
    ]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await query.message.edit_text(
        message, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def process_deploy_field_input(
    update: Update, context: Context, user_input: str
) -> None:
    """Process user input for a deploy field"""
    field_name = context.user_data.get("editing_deploy_field")

    if not field_name:
        await update.message.answer(
            ui.notice("warning", "No field selected", "Pick a field and try again."),
            parse_mode="MarkdownV2",
        )
        return

    deploy_params = context.user_data.get("deploy_params", DEPLOY_DEFAULTS.copy())

    try:
        # Parse the value based on field type
        if field_name in ["max_global_drawdown_quote", "max_controller_drawdown_quote"]:
            value = float(user_input) if user_input.strip() else None
        else:
            value = user_input.strip()

        # Set the value
        deploy_params[field_name] = value
        context.user_data["deploy_params"] = deploy_params

        # Clear field editing state
        context.user_data.pop("editing_deploy_field", None)
        context.user_data["bots_state"] = "deploy_configure"

        # Show confirmation
        label = field_name.replace("_", " ").title()
        await update.message.answer(
            ui.notice("success", f"{label} set to {value}"), parse_mode="MarkdownV2"
        )

        # Show button to return to form
        keyboard = [
            [
                InlineKeyboardButton(
                    text="Continue", callback_data="bots:deploy_form_back"
                )
            ]
        ]
        await update.message.answer(
            "Value updated\\. Tap to continue\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )

    except ValueError as e:
        await update.message.answer(
            ui.notice("error", "Invalid value", "Enter a valid number."),
            parse_mode="MarkdownV2",
        )


async def handle_execute_deploy(update: Update, context: Context) -> None:
    """Execute the deployment of selected controllers"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    deploy_params = context.user_data.get("deploy_params", {})

    instance_name = deploy_params.get("instance_name")
    credentials_profile = deploy_params.get("credentials_profile")
    controllers_config = deploy_params.get("controllers_config", [])

    if not instance_name or not credentials_profile:
        await query.answer(
            "Instance name and credentials are required", show_alert=True
        )
        return

    if not controllers_config:
        await query.answer("No controllers selected", show_alert=True)
        return

    # Show deploying message FIRST (before the long operation)
    controllers_str = ", ".join(
        [f"`{escape_markdown_v2(c)}`" for c in controllers_config]
    )
    await query.message.edit_text(
        f"*Deploying\\.\\.\\.*\n\n"
        f"*Instance:* `{escape_markdown_v2(instance_name)}`\n"
        f"*Controllers:*\n{controllers_str}\n\n"
        f"Please wait, this may take a moment\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

        # Deploy using deploy_v2_controllers (this can take time)
        result = await client.bot_orchestration.deploy_v2_controllers(
            instance_name=instance_name,
            credentials_profile=credentials_profile,
            controllers_config=controllers_config,
            max_global_drawdown_quote=deploy_params.get("max_global_drawdown_quote"),
            max_controller_drawdown_quote=deploy_params.get(
                "max_controller_drawdown_quote"
            ),
            image=deploy_params.get("image", "hummingbot/hummingbot:latest"),
        )

        # Clear deploy state
        context.user_data.pop("selected_controllers", None)
        context.user_data.pop("deploy_params", None)
        context.user_data.pop("bots_state", None)

        keyboard = [
            [InlineKeyboardButton(text="👁 View Bots", callback_data="bots:main_menu")],
            [
                InlineKeyboardButton(
                    text="🚀 Deploy More", callback_data="bots:deploy_menu"
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        status = result.get("status", "unknown")
        message = result.get("message", "")

        # Check for success - either status is "success" or message indicates success
        is_success = (
            status == "success"
            or "successfully" in message.lower()
            or "created" in message.lower()
        )
        # The one deed that also claims ownership: the subject is what
        # ``owned_bots.json`` is keyed on, so a bot deployed from Telegram is as
        # attributable as one a tick deployed (CORR-622).
        record_telegram_deed(
            update,
            verb="manage_bots:deploy",
            summary=(
                f"Deploy bot '{instance_name}' with controllers {controllers_config}"
            ),
            subject=instance_name,
            ok=is_success,
            error=message,
        )

        if is_success:
            await query.message.edit_text(
                f"*Deployment Started\\!*\n\n"
                f"*Instance:* `{escape_markdown_v2(instance_name)}`\n"
                f"*Controllers:*\n{controllers_str}\n\n"
                f"The bot is being deployed\\. Check status in Bots menu\\.",
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )
        else:
            error_msg = message or "Unknown error"
            await query.message.edit_text(
                f"*Deployment Failed*\n\n" f"Error: {escape_markdown_v2(error_msg)}",
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )

    except Exception as e:
        logger.error(f"Error deploying controllers: {e}", exc_info=True)
        # Use message edit instead of query.answer (which may have expired)
        keyboard = [
            [
                InlineKeyboardButton(
                    text="🔁 Try Again", callback_data="bots:execute_deploy"
                )
            ],
            [
                InlineKeyboardButton(
                    text="« Back", callback_data="bots:deploy_form_back"
                )
            ],
        ]
        await query.message.edit_text(
            f"*Deployment Failed*\n\n" f"Error: {escape_markdown_v2(str(e)[:200])}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
