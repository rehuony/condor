"""Custom config file upload."""

import logging

from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from utils.telegram_formatters import escape_markdown_v2, format_error_message

from .._shared import (
    clean_config_for_save,
    clear_bots_state,
    get_bots_client,
)
from .configs_menu import show_controller_configs_menu

logger = logging.getLogger(__name__)


async def show_upload_config_prompt(update: Update, context: Context) -> None:
    """Show message prompting user to upload a YAML config file"""
    query = update.callback_query

    # Set state to expect file upload
    context.user_data["bots_state"] = "awaiting_config_upload"

    message_text = (
        r"*Upload Custom Config*" + "\n\n"
        r"Upload a YAML file \(`.yml` or `.yaml`\) with your controller configuration\."
        + "\n\n"
        r"The file should contain a valid controller config with at least an `id` field\."
    )

    keyboard = [
        [InlineKeyboardButton(text="❌ Cancel", callback_data="bots:upload_cancel")],
    ]

    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_upload_cancel(update: Update, context: Context) -> None:
    """Cancel the upload and return to configs menu"""
    clear_bots_state(context)
    await show_controller_configs_menu(update, context)


async def handle_config_file_upload(update: Update, context: Context) -> None:
    """Handle uploaded YAML config file"""
    import yaml

    # Only process if we're expecting a config upload
    if context.user_data.get("bots_state") != "awaiting_config_upload":
        return

    chat_id = update.effective_chat.id
    document = update.message.document

    # Check file extension
    file_name = document.file_name or ""
    if not file_name.lower().endswith((".yml", ".yaml")):
        await update.message.answer(
            format_error_message("Please upload a YAML file (.yml or .yaml)"),
            parse_mode="MarkdownV2",
        )
        return

    try:
        # Download the file
        file = await context.bot.get_file(document.file_id)
        file_bytes = (await context.bot.download_file(file.file_path)).read()
        content = file_bytes.decode("utf-8")

        # Parse YAML
        try:
            config = yaml.safe_load(content)
        except yaml.YAMLError as e:
            await update.message.answer(
                format_error_message(f"Invalid YAML file: {str(e)}"),
                parse_mode="MarkdownV2",
            )
            return

        if not isinstance(config, dict):
            await update.message.answer(
                format_error_message("YAML file must contain a dictionary/object"),
                parse_mode="MarkdownV2",
            )
            return

        # Validate minimum required field
        config_id = config.get("id")
        if not config_id:
            await update.message.answer(
                format_error_message("Config must have an 'id' field"),
                parse_mode="MarkdownV2",
            )
            return

        # Save to backend
        client, _ = await get_bots_client(chat_id, context.user_data)
        result = await client.controllers.create_or_update_controller_config(
            config_id, clean_config_for_save(config)
        )

        # Clear state
        clear_bots_state(context)

        # Check result
        if result.get("status") == "success" or "success" in str(result).lower():
            controller_name = config.get("controller_name", "unknown")
            success_msg = (
                f"✅ *Config uploaded successfully\\!*\n\n"
                f"ID: `{escape_markdown_v2(config_id)}`\n"
                f"Type: `{escape_markdown_v2(controller_name)}`"
            )
            keyboard = [
                [
                    InlineKeyboardButton(
                        text="📁 View Configs", callback_data="bots:controller_configs"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Back to Menu", callback_data="bots:main_menu"
                    )
                ],
            ]
            await update.message.answer(
                success_msg,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )
        else:
            error_detail = result.get("message", result.get("error", str(result)))
            await update.message.answer(
                format_error_message(f"Failed to save config: {error_detail}"),
                parse_mode="MarkdownV2",
            )

    except Exception as e:
        logger.error(f"Error uploading config file: {e}", exc_info=True)
        clear_bots_state(context)
        await update.message.answer(
            format_error_message(f"Failed to upload config: {str(e)}"),
            parse_mode="MarkdownV2",
        )
