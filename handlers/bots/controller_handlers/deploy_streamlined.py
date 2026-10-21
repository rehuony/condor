"""Streamlined deploy flow: credentials, image, instance name, confirm."""

import logging
from typing import List

from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from handlers._deeds import record_telegram_deed
from utils.telegram_formatters import escape_markdown_v2

from .._shared import (
    get_bots_client,
)

logger = logging.getLogger(__name__)


# Available docker images
AVAILABLE_IMAGES = [
    "hummingbot/hummingbot:latest",
    "hummingbot/hummingbot:development",
]


async def _get_available_credentials(client) -> List[str]:
    """Fetch list of available credential profiles from the backend"""
    try:
        accounts = await client.accounts.list_accounts()
        return accounts if accounts else ["master_account"]
    except Exception as e:
        logger.warning(f"Could not fetch accounts, using default: {e}")
        return ["master_account"]


async def show_deploy_config_step(update: Update, context: Context) -> None:
    """Show streamlined deploy configuration with clickable buttons for name, credentials, and image"""
    query = update.callback_query

    selected = context.user_data.get("selected_controllers", set())
    configs = context.user_data.get("controller_configs_list", [])

    if not selected:
        await query.answer("No controllers selected", show_alert=True)
        return

    # Get selected config names
    controller_names = [
        configs[i].get("id", configs[i].get("config_name", f"config_{i}"))
        for i in selected
        if i < len(configs)
    ]

    # Initialize or get deploy params
    deploy_params = context.user_data.get("deploy_params", {})
    if not deploy_params.get("controllers_config"):
        creds_default = "master_account"
        deploy_params = {
            "controllers_config": controller_names,
            "credentials_profile": creds_default,
            "image": "hummingbot/hummingbot:latest",
            "instance_name": creds_default,  # Default name = credentials profile
        }
    context.user_data["deploy_params"] = deploy_params
    context.user_data["deploy_message_id"] = query.message.message_id
    context.user_data["deploy_chat_id"] = query.message.chat.id

    # Build message
    creds = deploy_params.get("credentials_profile", "master_account")
    image = deploy_params.get("image", "hummingbot/hummingbot:latest")
    instance_name = deploy_params.get("instance_name", creds)

    # Build controllers list in code block for readability
    controllers_block = "\n".join(controller_names)
    image_short = image.split("/")[-1] if "/" in image else image

    lines = [
        r"*🚀 Deploy Controllers*",
        "",
        "```",
        controllers_block,
        "```",
        "",
        r"*Configuration*",
        "",
        f"  📝  *Name:*      `{escape_markdown_v2(instance_name)}`",
        f"  👤  *Account:*   `{escape_markdown_v2(creds)}`",
        f"  🐳  *Image:*     `{escape_markdown_v2(image_short)}`",
        "",
        r"_Tap buttons below to change settings_",
    ]

    # Build keyboard - one button per row for better readability
    keyboard = [
        [
            InlineKeyboardButton(
                text=f"📝 Name: {instance_name[:25]}",
                callback_data="bots:select_name:_show",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"👤 Account: {creds}", callback_data="bots:select_creds:_show"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"🐳 Image: {image_short}", callback_data="bots:select_image:_show"
            )
        ],
        [
            InlineKeyboardButton(
                text="✅ Deploy Now", callback_data="bots:execute_deploy"
            )
        ],
        [InlineKeyboardButton(text="« Back", callback_data="bots:deploy_menu")],
    ]

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    # Set drawdowns to None (skip them)
    deploy_params["max_global_drawdown_quote"] = None
    deploy_params["max_controller_drawdown_quote"] = None
    context.user_data["deploy_params"] = deploy_params

    await query.message.edit_text(
        "\n".join(lines), parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_select_credentials(
    update: Update, context: Context, creds: str
) -> None:
    """Handle credentials profile selection"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    if creds == "_show":
        # Show available credentials profiles
        try:
            client, _ = await get_bots_client(chat_id, context.user_data)
            available_creds = await _get_available_credentials(client)
        except Exception:
            available_creds = ["master_account"]

        deploy_params = context.user_data.get("deploy_params", {})
        current = deploy_params.get("credentials_profile", "master_account")

        lines = [
            r"*Select Credentials Profile*",
            "",
            f"Current: `{escape_markdown_v2(current)}`",
            "",
            r"_Choose an account to deploy with:_",
        ]

        # Build buttons for each credential profile
        keyboard = []
        for acc in available_creds:
            marker = "✓ " if acc == current else ""
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=f"{marker}{acc}", callback_data=f"bots:select_creds:{acc}"
                    )
                ]
            )

        keyboard.append(
            [
                InlineKeyboardButton(text="« Back", callback_data="bots:deploy_config"),
            ]
        )

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
    else:
        # Set the selected credential profile
        deploy_params = context.user_data.get("deploy_params", {})
        deploy_params["credentials_profile"] = creds
        context.user_data["deploy_params"] = deploy_params

        await query.answer(f"Account set to {creds}")
        await show_deploy_config_step(update, context)


async def handle_select_image(update: Update, context: Context, image: str) -> None:
    """Handle docker image selection"""
    query = update.callback_query

    if image == "_show":
        # Show available images
        deploy_params = context.user_data.get("deploy_params", {})
        current = deploy_params.get("image", "hummingbot/hummingbot:latest")

        lines = [
            r"*Select Docker Image*",
            "",
            f"Current: `{escape_markdown_v2(current)}`",
            "",
            r"_Choose an image to deploy with:_",
        ]

        # Build buttons for each image
        keyboard = []
        for img in AVAILABLE_IMAGES:
            marker = "✓ " if img == current else ""
            img_short = img.split("/")[-1] if "/" in img else img
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=f"{marker}{img_short}",
                        callback_data=f"bots:select_image:{img}",
                    )
                ]
            )

        keyboard.append(
            [
                InlineKeyboardButton(text="« Back", callback_data="bots:deploy_config"),
            ]
        )

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
    else:
        # Set the selected image
        deploy_params = context.user_data.get("deploy_params", {})
        deploy_params["image"] = image
        context.user_data["deploy_params"] = deploy_params

        img_short = image.split("/")[-1] if "/" in image else image
        await query.answer(f"Image set to {img_short}")
        await show_deploy_config_step(update, context)


async def handle_select_instance_name(
    update: Update, context: Context, name: str
) -> None:
    """Handle instance name selection/editing"""
    query = update.callback_query

    if name == "_show":
        # Show name editing prompt
        deploy_params = context.user_data.get("deploy_params", {})
        creds = deploy_params.get("credentials_profile", "master_account")
        current = deploy_params.get("instance_name", creds)

        lines = [
            r"*Edit Instance Name*",
            "",
            f"Current: `{escape_markdown_v2(current)}`",
            "",
            r"_Send a new name or choose an option:_",
        ]

        keyboard = [
            [
                InlineKeyboardButton(
                    text=f"✓ Use: {creds}", callback_data=f"bots:select_name:{creds}"
                )
            ],
            [InlineKeyboardButton(text="« Back", callback_data="bots:deploy_config")],
        ]

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )

        # Set state to allow custom name input
        context.user_data["bots_state"] = "deploy_edit_name"
    else:
        # Set the selected name
        deploy_params = context.user_data.get("deploy_params", {})
        deploy_params["instance_name"] = name
        context.user_data["deploy_params"] = deploy_params
        context.user_data["bots_state"] = None

        await query.answer(f"Name set to {name[:25]}")
        await show_deploy_config_step(update, context)


async def process_instance_name_input(
    update: Update, context: Context, user_input: str
) -> None:
    """Process custom instance name input from user text message"""
    try:
        await update.message.delete()
    except:
        pass

    custom_name = user_input.strip()
    if not custom_name:
        return

    # Set the custom name
    deploy_params = context.user_data.get("deploy_params", {})
    deploy_params["instance_name"] = custom_name
    context.user_data["deploy_params"] = deploy_params
    context.user_data["bots_state"] = None

    # Update the config step message
    message_id = context.user_data.get("deploy_message_id")
    chat_id = context.user_data.get("deploy_chat_id")

    if message_id and chat_id:
        # Create a fake update/query to reuse show_deploy_config_step logic
        # We need to update the existing message, so we'll do it manually
        creds = deploy_params.get("credentials_profile", "master_account")
        image = deploy_params.get("image", "hummingbot/hummingbot:latest")
        controllers = deploy_params.get("controllers_config", [])

        controllers_block = "\n".join(controllers)
        image_short = image.split("/")[-1] if "/" in image else image

        lines = [
            r"*🚀 Deploy Controllers*",
            "",
            "```",
            controllers_block,
            "```",
            "",
            f"*Name:*     `{escape_markdown_v2(custom_name)}`",
            f"*Account:*  `{escape_markdown_v2(creds)}`",
            f"*Image:*    `{escape_markdown_v2(image_short)}`",
            "",
            r"_Tap buttons below to change settings_",
        ]

        keyboard = [
            [
                InlineKeyboardButton(
                    text=f"📝 Name: {custom_name[:25]}",
                    callback_data="bots:select_name:_show",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"👤 Account: {creds}", callback_data="bots:select_creds:_show"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🐳 Image: {image_short}",
                    callback_data="bots:select_image:_show",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Deploy Now", callback_data="bots:execute_deploy"
                )
            ],
            [InlineKeyboardButton(text="« Back", callback_data="bots:deploy_menu")],
        ]

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="\n".join(lines),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )
        except Exception as e:
            logger.error(f"Error updating deploy config message: {e}")


async def handle_deploy_confirm(update: Update, context: Context) -> None:
    """Show deployment confirmation with auto-generated instance name"""
    query = update.callback_query

    deploy_params = context.user_data.get("deploy_params", {})
    controllers = deploy_params.get("controllers_config", [])
    creds = deploy_params.get("credentials_profile", "master_account")
    image = deploy_params.get("image", "hummingbot/hummingbot:latest")

    if not controllers:
        await query.answer("No controllers selected", show_alert=True)
        return

    # Instance name is just the credentials profile - API adds timestamp
    generated_name = creds

    # Store for later use
    context.user_data["deploy_generated_name"] = generated_name

    controllers_str = "\n".join([f"• `{escape_markdown_v2(c)}`" for c in controllers])
    image_short = image.split("/")[-1] if "/" in image else image

    lines = [
        r"*Confirm Deployment*",
        "",
        r"*Controllers:*",
        controllers_str,
        "",
        f"*Account:* `{escape_markdown_v2(creds)}`",
        f"*Image:* `{escape_markdown_v2(image_short)}`",
        "",
        r"*Instance Name:*",
        f"`{escape_markdown_v2(generated_name)}`",
        "",
        r"_Click the name to deploy, or send a custom name_",
    ]

    keyboard = [
        [
            InlineKeyboardButton(
                text=f"✅ Deploy as {generated_name[:25]}",
                callback_data="bots:execute_deploy",
            ),
        ],
        [
            InlineKeyboardButton(text="« Back", callback_data="bots:deploy_config"),
        ],
    ]

    # Set state to allow custom name input
    context.user_data["bots_state"] = "deploy_custom_name"

    # Store the generated name in deploy_params
    deploy_params["instance_name"] = generated_name
    # Set drawdowns to None (skip them)
    deploy_params["max_global_drawdown_quote"] = None
    deploy_params["max_controller_drawdown_quote"] = None
    context.user_data["deploy_params"] = deploy_params

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_deploy_custom_name(update: Update, context: Context) -> None:
    """Handle custom instance name input (called from message handler)"""
    # This is triggered via message handler when in deploy_custom_name state
    pass  # The actual processing happens in process_deploy_custom_name_input


async def process_deploy_custom_name_input(
    update: Update, context: Context, user_input: str
) -> None:
    """Process custom instance name input and execute deployment"""
    try:
        await update.message.delete()
    except:
        pass

    custom_name = user_input.strip()
    if not custom_name:
        return

    deploy_params = context.user_data.get("deploy_params", {})
    deploy_params["instance_name"] = custom_name
    context.user_data["deploy_params"] = deploy_params

    # Execute deployment with custom name
    message_id = context.user_data.get("deploy_message_id")
    chat_id = context.user_data.get("deploy_chat_id")

    if not message_id or not chat_id:
        return

    controllers = deploy_params.get("controllers_config", [])
    creds = deploy_params.get("credentials_profile", "master_account")
    image = deploy_params.get("image", "hummingbot/hummingbot:latest")

    controllers_str = ", ".join([f"`{escape_markdown_v2(c)}`" for c in controllers])

    # Update message to show deploying
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                f"*Deploying\\.\\.\\.*\n\n"
                f"*Instance:* `{escape_markdown_v2(custom_name)}`\n"
                f"*Controllers:* {controllers_str}\n\n"
                f"Please wait, this may take a moment\\.\\.\\."
            ),
            parse_mode="MarkdownV2",
        )
    except Exception as e:
        logger.error(f"Error updating deploy message: {e}")

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

        result = await client.bot_orchestration.deploy_v2_controllers(
            instance_name=custom_name,
            credentials_profile=creds,
            controllers_config=controllers,
            max_global_drawdown_quote=None,
            max_controller_drawdown_quote=None,
            image=image,
        )

        # Clear deploy state
        context.user_data.pop("selected_controllers", None)
        context.user_data.pop("deploy_params", None)
        context.user_data.pop("bots_state", None)
        context.user_data.pop("deploy_generated_name", None)

        keyboard = [
            [InlineKeyboardButton(text="👁 View Bots", callback_data="bots:main_menu")],
            [
                InlineKeyboardButton(
                    text="🚀 Deploy More", callback_data="bots:deploy_menu"
                )
            ],
        ]

        status = result.get("status", "unknown")
        message = result.get("message", "")
        is_success = (
            status == "success"
            or "successfully" in message.lower()
            or "created" in message.lower()
        )
        record_telegram_deed(
            update,
            verb="manage_bots:deploy",
            summary=f"Deploy bot '{custom_name}' with controllers {controllers}",
            subject=custom_name,
            ok=is_success,
            error=message,
        )

        if is_success:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"*Deployment Started\\!*\n\n"
                    f"*Instance:* `{escape_markdown_v2(custom_name)}`\n"
                    f"*Controllers:* {controllers_str}\n\n"
                    f"The bot is being deployed\\. Check status in Bots menu\\."
                ),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )
        else:
            error_msg = message or "Unknown error"
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"*Deployment Failed*\n\n" f"Error: {escape_markdown_v2(error_msg)}"
                ),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )

    except Exception as e:
        logger.error(f"Error deploying with custom name: {e}", exc_info=True)
        keyboard = [
            [
                InlineKeyboardButton(
                    text="🔁 Try Again", callback_data="bots:deploy_confirm"
                )
            ],
            [InlineKeyboardButton(text="« Back", callback_data="bots:deploy_config")],
        ]
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                f"*Deployment Failed*\n\n" f"Error: {escape_markdown_v2(str(e)[:200])}"
            ),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
