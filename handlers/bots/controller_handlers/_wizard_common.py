"""Steps the Grid Strike, PMM Mister and PMM V1 wizards share.

Each wizard describes itself with a :class:`WizardSpec` (state-key prefix,
callback prefixes, title) and hands it to these functions; the per-wizard
modules keep thin named wrappers so the router sees the same handler names.
"""

import logging
from dataclasses import dataclass
from typing import Callable

from condor.telegram.types import (
    NO_LINK_PREVIEW,
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from utils.telegram_formatters import escape_markdown_v2, format_error_message

from .._shared import (
    clean_config_for_save,
    clear_bots_state,
    get_available_cex_connectors,
    get_bots_client,
    get_controller_config,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WizardSpec:
    """Everything that differs between the wizards' shared steps."""

    key: str  # user_data prefix: "<key>_wizard_step", "<key>_wizard_message_id"
    title: str  # MarkdownV2-escaped display name, e.g. r"PMM Mister"
    log_name: str  # prefix for log lines, e.g. "PMM"
    connector_action: str  # "bots:pmm_connector:"
    pair_action: str  # "bots:pmm_pair:"
    pair_select_action: str  # "bots:pmm_pair_select:"
    back_to_connector: str  # "bots:pmm_back:connector"
    review_back: str = ""  # "bots:pmm_review_back"
    create_another: str = ""  # "bots:new_pmm_mister"

    @property
    def input_state(self) -> str:
        return f"{self.key}_wizard_input"

    @property
    def step_key(self) -> str:
        return f"{self.key}_wizard_step"

    @property
    def message_id_key(self) -> str:
        return f"{self.key}_wizard_message_id"

    @property
    def chat_id_key(self) -> str:
        return f"{self.key}_wizard_chat_id"


async def show_connector_step(
    update: Update, context: Context, spec: WizardSpec, prompt: str
) -> None:
    """Step 1 of every wizard: pick a CEX connector (two buttons per row)."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, server_name = await get_bots_client(chat_id, context.user_data)
        cex_connectors = await get_available_cex_connectors(
            context.user_data, client, server_name=server_name
        )

        if not cex_connectors:
            from handlers.config.api_keys import keys_web_button

            keys_btn = keys_web_button(query.from_user) or InlineKeyboardButton(
                text="🔑 View Keys", callback_data="config_api_keys"
            )
            keyboard = [
                [keys_btn],
                [InlineKeyboardButton(text="« Back", callback_data="bots:main_menu")],
            ]
            await query.message.edit_text(
                rf"*{spec.title} \- New Config*" + "\n\n"
                r"⚠️ No CEX connectors available\." + "\n\n"
                r"You need to connect API keys for an exchange to deploy strategies\."
                + "\n"
                r"Connect them from the web dashboard \(Settings → Keys\)\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )
            return

        keyboard = []
        row = []
        for connector in cex_connectors:
            row.append(
                InlineKeyboardButton(
                    text=f"🏦 {connector}",
                    callback_data=f"{spec.connector_action}{connector}",
                )
            )
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append(
            [InlineKeyboardButton(text="❌ Cancel", callback_data="bots:main_menu")]
        )

        await query.message.edit_text(
            prompt,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            link_preview_options=NO_LINK_PREVIEW,
        )

    except Exception as e:
        logger.error(f"Error in {spec.log_name} connector step: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton(text="Back", callback_data="bots:main_menu")]]
        await query.message.edit_text(
            format_error_message(f"Error: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )


async def show_pair_step(
    update: Update,
    context: Context,
    spec: WizardSpec,
    prompt: Callable[[str, list[str]], str],
) -> None:
    """Step 2 of every wizard: pick a recent pair or type one.

    ``prompt(connector, recent_pairs)`` renders the wizard-specific message.
    """
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    context.user_data["bots_state"] = spec.input_state
    context.user_data[spec.step_key] = "trading_pair"

    # Recent pairs from existing configs (max 6, most recent first)
    existing_configs = context.user_data.get("controller_configs_list", [])
    recent_pairs = []
    seen = set()
    for cfg in reversed(existing_configs):
        pair = cfg.get("trading_pair", "")
        if pair and pair not in seen:
            seen.add(pair)
            recent_pairs.append(pair)
            if len(recent_pairs) >= 6:
                break

    keyboard = []
    if recent_pairs:
        row = []
        for pair in recent_pairs:
            row.append(
                InlineKeyboardButton(
                    text=pair, callback_data=f"{spec.pair_action}{pair}"
                )
            )
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
    keyboard.append(
        [
            InlineKeyboardButton(text="⬅️ Back", callback_data=spec.back_to_connector),
            InlineKeyboardButton(text="❌ Cancel", callback_data="bots:main_menu"),
        ]
    )

    await query.message.edit_text(
        prompt(connector, recent_pairs),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def show_pair_suggestions(
    update: Update,
    context: Context,
    spec: WizardSpec,
    error_msg: str,
    suggestions: list,
) -> None:
    """Offer close matches when a typed pair fails validation."""
    message_id = context.user_data.get(spec.message_id_key)
    chat_id = context.user_data.get(spec.chat_id_key)

    help_text = f"❌ *{escape_markdown_v2(error_msg)}*\n\n"
    if suggestions:
        help_text += "💡 *Did you mean:*\n"
    else:
        help_text += "_No similar pairs found\\._\n"

    keyboard = []
    for pair in suggestions:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"📈 {pair}",
                    callback_data=f"{spec.pair_select_action}{pair}",
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(text="⬅️ Back", callback_data=spec.back_to_connector),
            InlineKeyboardButton(text="❌ Cancel", callback_data="bots:main_menu"),
        ]
    )
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    if message_id and chat_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=help_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.debug(f"Could not update {spec.log_name} wizard message: {e}")
    else:
        await update.effective_chat.send_message(
            help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def save_config(
    update: Update,
    context: Context,
    spec: WizardSpec,
    validate: Callable[[dict], tuple[bool, str | None]],
) -> None:
    """Validate the wizard's config and create or update it on the server."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    is_valid, error = validate(config)
    if not is_valid:
        keyboard = [
            [InlineKeyboardButton(text="⬅️ Back", callback_data=spec.review_back)]
        ]
        await query.message.edit_text(
            f"*Validation Error*\n\n{escape_markdown_v2(error or 'Unknown error')}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
        return

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        config_id = config.get("id", "")
        result = await client.controllers.create_or_update_controller_config(
            config_id, clean_config_for_save(config)
        )

        if result.get("status") == "success" or "success" in str(result).lower():
            keyboard = [
                [
                    InlineKeyboardButton(
                        text="Create Another", callback_data=spec.create_another
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Deploy Now", callback_data="bots:deploy_menu"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Back to Menu", callback_data="bots:controller_configs"
                    )
                ],
            ]
            await query.message.edit_text(
                r"*✅ Config Saved\!*" + "\n\n"
                f"*ID:* `{escape_markdown_v2(config.get('id', ''))}`",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )
            clear_bots_state(context)
        else:
            error_msg = result.get("message", str(result))
            keyboard = [
                [InlineKeyboardButton(text="⬅️ Back", callback_data=spec.review_back)]
            ]
            await query.message.edit_text(
                f"*Save Failed*\n\n{escape_markdown_v2(error_msg[:200])}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )

    except Exception as e:
        logger.error(f"Error saving {spec.log_name} config: {e}", exc_info=True)
        keyboard = [
            [InlineKeyboardButton(text="⬅️ Back", callback_data=spec.review_back)]
        ]
        await query.message.edit_text(
            f"*Error*\n\n{escape_markdown_v2(str(e)[:200])}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )


__all__ = [
    "WizardSpec",
    "show_connector_step",
    "show_pair_step",
    "show_pair_suggestions",
    "save_config",
]
