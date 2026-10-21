"""PMM V1 wizard."""

import logging

from condor.telegram.errors import is_not_modified
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from handlers.cex._shared import (
    get_correct_pair_format,
    get_trading_rules,
    validate_trading_pair,
)
from utils.telegram_formatters import escape_markdown_v2

from .._shared import (
    get_bots_client,
    get_controller_config,
    init_new_controller_config,
    set_controller_config,
)
from ..controllers.pmm_v1 import generate_id as pv1_generate_id
from ..controllers.pmm_v1 import validate_config as pv1_validate_config
from ._wizard_common import (
    WizardSpec,
    save_config,
    show_connector_step,
    show_pair_step,
    show_pair_suggestions,
)

PV1_WIZARD = WizardSpec(
    key="pv1",
    title=r"PMM V1",
    log_name="PMM V1",
    connector_action="bots:pv1_connector:",
    pair_action="bots:pv1_pair:",
    pair_select_action="bots:pv1_pair_select:",
    back_to_connector="bots:pv1_back:connector",
    review_back="bots:pv1_review_back",
    create_another="bots:new_pmm_v1",
)

logger = logging.getLogger(__name__)


async def show_new_pmm_v1_form(update: Update, context: Context) -> None:
    """Start the progressive PMM V1 wizard - Step 1: Connector"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.list_controller_configs()
        context.user_data["controller_configs_list"] = configs
    except Exception as e:
        logger.warning(f"Could not fetch existing configs: {e}")

    config = init_new_controller_config(context, "pmm_v1")
    context.user_data["bots_state"] = "pv1_wizard"
    context.user_data["pv1_wizard_step"] = "connector_name"
    context.user_data["pv1_wizard_message_id"] = query.message.message_id
    context.user_data["pv1_wizard_chat_id"] = query.message.chat.id

    await _show_pv1_wizard_connector_step(update, context)


async def _show_pv1_wizard_connector_step(update: Update, context: Context) -> None:
    """PMM V1 Wizard Step 1: Select Connector"""
    await show_connector_step(
        update,
        context,
        PV1_WIZARD,
        r"*📈 PMM V1 \- New Config*" + "\n\n" r"*Step 1/5:* 🏦 Select Connector",
    )


async def handle_pv1_wizard_connector(
    update: Update, context: Context, connector: str
) -> None:
    """Handle connector selection"""
    config = get_controller_config(context)
    config["connector_name"] = connector
    set_controller_config(context, config)
    context.user_data["pv1_wizard_step"] = "trading_pair"
    await _show_pv1_wizard_pair_step(update, context)


async def _show_pv1_wizard_pair_step(update: Update, context: Context) -> None:
    """PMM V1 Wizard Step 2: Trading Pair"""

    def prompt(connector: str, recent_pairs: list[str]) -> str:
        return (
            r"*📈 PMM V1 \- New Config*" + "\n\n"
            f"*Connector:* `{escape_markdown_v2(connector)}`" + "\n\n"
            r"*Step 2/5:* 🔗 Trading Pair" + "\n\n"
            r"Select or type a pair:"
        )

    await show_pair_step(update, context, PV1_WIZARD, prompt)


async def handle_pv1_wizard_pair(update: Update, context: Context, pair: str) -> None:
    """Handle pair selection"""
    config = get_controller_config(context)
    config["trading_pair"] = pair.upper()
    set_controller_config(context, config)
    context.user_data["pv1_wizard_step"] = "order_amount"
    await _show_pv1_wizard_amount_step(update, context)


async def _show_pv1_wizard_amount_step(update: Update, context: Context) -> None:
    """PMM V1 Wizard Step 3: Order Amount (Base)"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")

    context.user_data["bots_state"] = "pv1_wizard_input"
    context.user_data["pv1_wizard_step"] = "order_amount"

    keyboard = [
        [
            InlineKeyboardButton(text="0.001", callback_data="bots:pv1_amount:0.001"),
            InlineKeyboardButton(text="0.01", callback_data="bots:pv1_amount:0.01"),
            InlineKeyboardButton(text="0.1", callback_data="bots:pv1_amount:0.1"),
        ],
        [
            InlineKeyboardButton(text="1", callback_data="bots:pv1_amount:1"),
            InlineKeyboardButton(text="10", callback_data="bots:pv1_amount:10"),
            InlineKeyboardButton(text="100", callback_data="bots:pv1_amount:100"),
        ],
        [
            InlineKeyboardButton(text="« Back", callback_data="bots:pv1_back:pair"),
            InlineKeyboardButton(text="✕ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    # Extract base token for label
    base_token = pair.split("-")[0] if "-" in pair else "base"

    await query.message.edit_text(
        r"*📈 PMM V1 \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n\n"
        f"*Step 3/5:* 💰 Order Amount \\({escape_markdown_v2(base_token)}\\)" + "\n\n"
        r"Select or type amount:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_pv1_wizard_amount(
    update: Update, context: Context, amount: str
) -> None:
    """Handle amount selection"""
    config = get_controller_config(context)
    config["order_amount"] = amount
    set_controller_config(context, config)
    context.user_data["pv1_wizard_step"] = "spreads"
    await _show_pv1_wizard_spreads_step(update, context)


async def _show_pv1_wizard_spreads_step(update: Update, context: Context) -> None:
    """PMM V1 Wizard Step 4: Spread"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    amount = config.get("order_amount", "0.001")

    context.user_data["bots_state"] = "pv1_wizard_input"
    context.user_data["pv1_wizard_step"] = "spreads"

    keyboard = [
        [
            InlineKeyboardButton(text="0.01%", callback_data="bots:pv1_spreads:0.0001"),
            InlineKeyboardButton(text="0.02%", callback_data="bots:pv1_spreads:0.0002"),
            InlineKeyboardButton(text="0.05%", callback_data="bots:pv1_spreads:0.0005"),
        ],
        [
            InlineKeyboardButton(text="0.1%", callback_data="bots:pv1_spreads:0.001"),
            InlineKeyboardButton(text="0.2%", callback_data="bots:pv1_spreads:0.002"),
            InlineKeyboardButton(text="0.5%", callback_data="bots:pv1_spreads:0.005"),
        ],
        [
            InlineKeyboardButton(text="« Back", callback_data="bots:pv1_back:amount"),
            InlineKeyboardButton(text="✕ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await query.message.edit_text(
        r"*📈 PMM V1 \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n"
        f"💰 Amount: `{escape_markdown_v2(str(amount))}`" + "\n\n"
        r"*Step 4/5:* 📊 Spread" + "\n\n"
        r"_Applied to both buy and sell\. Or type a custom value \(e\.g\. 0\.001\)_",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_pv1_wizard_spreads(
    update: Update, context: Context, spread: str
) -> None:
    """Handle spread selection - applies single spread to both buy and sell"""
    config = get_controller_config(context)
    spread_val = float(spread)
    config["buy_spreads"] = [spread_val]
    config["sell_spreads"] = [spread_val]
    set_controller_config(context, config)
    context.user_data["pv1_wizard_step"] = "review"
    await _show_pv1_wizard_review_step(update, context)


async def _show_pv1_wizard_review_step(update: Update, context: Context) -> None:
    """PMM V1 Wizard Step 5: Review with copyable config format"""
    query = update.callback_query
    config = get_controller_config(context)

    # Generate ID if not set
    if not config.get("id"):
        existing = context.user_data.get("controller_configs_list", [])
        config["id"] = pv1_generate_id(config, existing)
        set_controller_config(context, config)

    context.user_data["bots_state"] = "pv1_wizard_input"
    context.user_data["pv1_wizard_step"] = "review"

    # Build copyable config block
    config_block = (
        f"id: {config.get('id', '')}\n"
        f"connector_name: {config.get('connector_name', '')}\n"
        f"trading_pair: {config.get('trading_pair', '')}\n"
        f"order_amount: {config.get('order_amount', '0.001')}\n"
        f"buy_spreads: {config.get('buy_spreads', [0.0002])}\n"
        f"sell_spreads: {config.get('sell_spreads', [0.0002])}\n"
        f"order_refresh_time: {config.get('order_refresh_time', 30)}\n"
        f"order_refresh_tolerance_pct: {config.get('order_refresh_tolerance_pct', '-1')}\n"
        f"filled_order_delay: {config.get('filled_order_delay', 60)}\n"
        f"inventory_skew_enabled: {config.get('inventory_skew_enabled', False)}\n"
        f"inventory_range_multiplier: {config.get('inventory_range_multiplier', '1.0')}\n"
        f"price_ceiling: {config.get('price_ceiling', '-1')}\n"
        f"price_floor: {config.get('price_floor', '-1')}\n"
        f"manual_kill_switch: {config.get('manual_kill_switch', False)}"
    )

    pair = config.get("trading_pair", "")
    message_text = (
        f"*{escape_markdown_v2(pair)}* \\- Review Config\n\n"
        f"```\n{config_block}\n```\n\n"
        f"_To edit, send `field: value` lines_"
    )

    keyboard = [
        [InlineKeyboardButton(text="✅ Save Config", callback_data="bots:pv1_save")],
        [
            InlineKeyboardButton(text="« Back", callback_data="bots:pv1_back:spreads"),
            InlineKeyboardButton(text="✕ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_pv1_back(update: Update, context: Context, target: str) -> None:
    """Handle back navigation in PMM V1 wizard"""
    if target == "connector":
        await _show_pv1_wizard_connector_step(update, context)
    elif target == "pair":
        await _show_pv1_wizard_pair_step(update, context)
    elif target == "amount":
        await _show_pv1_wizard_amount_step(update, context)
    elif target == "spreads":
        await _show_pv1_wizard_spreads_step(update, context)


async def handle_pv1_save(update: Update, context: Context) -> None:
    """Save PMM V1 config"""
    await save_config(update, context, PV1_WIZARD, pv1_validate_config)


async def handle_pv1_review_back(update: Update, context: Context) -> None:
    """Back to review"""
    await _show_pv1_wizard_review_step(update, context)


async def handle_pv1_pair_select(
    update: Update, context: Context, trading_pair: str
) -> None:
    """Handle selection of a suggested trading pair in PMM V1 wizard"""
    config = get_controller_config(context)
    message_id = context.user_data.get("pv1_wizard_message_id")
    chat_id = context.user_data.get("pv1_wizard_chat_id")

    config["trading_pair"] = trading_pair
    set_controller_config(context, config)
    connector = config.get("connector_name", "")

    # Go to amount step
    context.user_data["pv1_wizard_step"] = "order_amount"
    context.user_data["bots_state"] = "pv1_wizard_input"

    base_token = trading_pair.split("-")[0] if "-" in trading_pair else "base"

    keyboard = [
        [
            InlineKeyboardButton(text="0.001", callback_data="bots:pv1_amount:0.001"),
            InlineKeyboardButton(text="0.01", callback_data="bots:pv1_amount:0.01"),
            InlineKeyboardButton(text="0.1", callback_data="bots:pv1_amount:0.1"),
        ],
        [
            InlineKeyboardButton(text="1", callback_data="bots:pv1_amount:1"),
            InlineKeyboardButton(text="10", callback_data="bots:pv1_amount:10"),
            InlineKeyboardButton(text="100", callback_data="bots:pv1_amount:100"),
        ],
        [
            InlineKeyboardButton(text="« Back", callback_data="bots:pv1_back:pair"),
            InlineKeyboardButton(text="✕ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=r"*📈 PMM V1 \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(trading_pair)}`"
        + "\n\n"
        f"*Step 3/5:* 💰 Order Amount \\({escape_markdown_v2(base_token)}\\)" + "\n\n"
        r"Select or type amount:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def _show_pv1_pair_suggestions(
    update: Update,
    context: Context,
    input_pair: str,
    error_msg: str,
    suggestions: list,
    connector: str,
) -> None:
    """Show trading pair suggestions when validation fails in PMM V1 wizard"""
    await show_pair_suggestions(update, context, PV1_WIZARD, error_msg, suggestions)


async def process_pv1_wizard_input(
    update: Update, context: Context, user_input: str
) -> None:
    """Process text input during PMM V1 wizard"""
    step = context.user_data.get("pv1_wizard_step", "")
    config = get_controller_config(context)
    message_id = context.user_data.get("pv1_wizard_message_id")
    chat_id = context.user_data.get("pv1_wizard_chat_id")

    try:
        await update.message.delete()
    except Exception:
        pass

    if step == "trading_pair":
        pair = user_input.upper().strip()
        if "-" not in pair:
            pair = pair.replace("/", "-").replace("_", "-")

        connector = config.get("connector_name", "")

        # Validate trading pair exists on the connector
        client, _ = await get_bots_client(chat_id, context.user_data)
        is_valid, error_msg, suggestions, correct_pair = await validate_trading_pair(
            context.user_data, client, connector, pair
        )

        if not is_valid:
            await _show_pv1_pair_suggestions(
                update, context, pair, error_msg, suggestions, connector
            )
            return

        # Use the correct pair format
        if correct_pair:
            pair = correct_pair
        else:
            trading_rules = await get_trading_rules(
                context.user_data, client, connector
            )
            fallback_pair = get_correct_pair_format(trading_rules, pair)
            if fallback_pair:
                pair = fallback_pair

        config["trading_pair"] = pair
        set_controller_config(context, config)

        # Go to amount step
        context.user_data["pv1_wizard_step"] = "order_amount"
        base_token = pair.split("-")[0] if "-" in pair else "base"

        keyboard = [
            [
                InlineKeyboardButton(
                    text="0.001", callback_data="bots:pv1_amount:0.001"
                ),
                InlineKeyboardButton(text="0.01", callback_data="bots:pv1_amount:0.01"),
                InlineKeyboardButton(text="0.1", callback_data="bots:pv1_amount:0.1"),
            ],
            [
                InlineKeyboardButton(text="1", callback_data="bots:pv1_amount:1"),
                InlineKeyboardButton(text="10", callback_data="bots:pv1_amount:10"),
                InlineKeyboardButton(text="100", callback_data="bots:pv1_amount:100"),
            ],
            [
                InlineKeyboardButton(text="« Back", callback_data="bots:pv1_back:pair"),
                InlineKeyboardButton(text="✕ Cancel", callback_data="bots:main_menu"),
            ],
        ]
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=r"*📈 PMM V1 \- New Config*" + "\n\n"
            f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
            + "\n\n"
            f"*Step 3/5:* 💰 Order Amount \\({escape_markdown_v2(base_token)}\\)"
            + "\n\n"
            r"Select or type amount:",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )

    elif step == "order_amount":
        try:
            amount_str = user_input.strip().replace(",", "")
            amount = float(amount_str)
            if amount <= 0:
                raise ValueError("Amount must be positive")
            config["order_amount"] = amount_str
            set_controller_config(context, config)
            context.user_data["pv1_wizard_step"] = "spreads"

            connector = config.get("connector_name", "")
            pair = config.get("trading_pair", "")

            keyboard = [
                [
                    InlineKeyboardButton(
                        text="0.01%", callback_data="bots:pv1_spreads:0.0001"
                    ),
                    InlineKeyboardButton(
                        text="0.02%", callback_data="bots:pv1_spreads:0.0002"
                    ),
                    InlineKeyboardButton(
                        text="0.05%", callback_data="bots:pv1_spreads:0.0005"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="0.1%", callback_data="bots:pv1_spreads:0.001"
                    ),
                    InlineKeyboardButton(
                        text="0.2%", callback_data="bots:pv1_spreads:0.002"
                    ),
                    InlineKeyboardButton(
                        text="0.5%", callback_data="bots:pv1_spreads:0.005"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="« Back", callback_data="bots:pv1_back:amount"
                    ),
                    InlineKeyboardButton(
                        text="✕ Cancel", callback_data="bots:main_menu"
                    ),
                ],
            ]
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=r"*📈 PMM V1 \- New Config*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
                + "\n"
                f"💰 Amount: `{escape_markdown_v2(amount_str)}`" + "\n\n"
                r"*Step 4/5:* 📊 Spread" + "\n\n"
                r"_Applied to both buy and sell\. Or type a custom value \(e\.g\. 0\.001\)_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )
        except ValueError:
            connector = config.get("connector_name", "")
            pair = config.get("trading_pair", "")
            base_token = pair.split("-")[0] if "-" in pair else "base"
            keyboard = [
                [
                    InlineKeyboardButton(
                        text="0.001", callback_data="bots:pv1_amount:0.001"
                    ),
                    InlineKeyboardButton(
                        text="0.01", callback_data="bots:pv1_amount:0.01"
                    ),
                    InlineKeyboardButton(
                        text="0.1", callback_data="bots:pv1_amount:0.1"
                    ),
                ],
                [
                    InlineKeyboardButton(text="1", callback_data="bots:pv1_amount:1"),
                    InlineKeyboardButton(text="10", callback_data="bots:pv1_amount:10"),
                    InlineKeyboardButton(
                        text="100", callback_data="bots:pv1_amount:100"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="« Back", callback_data="bots:pv1_back:pair"
                    ),
                    InlineKeyboardButton(
                        text="✕ Cancel", callback_data="bots:main_menu"
                    ),
                ],
            ]
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=r"*📈 PMM V1 \- New Config*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
                + "\n\n"
                f"*Step 3/5:* 💰 Order Amount \\({escape_markdown_v2(base_token)}\\)"
                + "\n\n"
                r"⚠️ _Invalid value\. Enter a positive number \(e\.g\. 0\.01\)_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )

    elif step == "spreads":
        try:
            spread_val = float(user_input.strip().replace("%", ""))
            # If user entered percentage like "0.02" meaning 0.02%, convert
            # But if they enter something like 0.0002, treat as raw fraction
            if spread_val <= 0:
                raise ValueError("Spread must be positive")
            config["buy_spreads"] = [spread_val]
            config["sell_spreads"] = [spread_val]
            set_controller_config(context, config)
            context.user_data["pv1_wizard_step"] = "review"

            # Show review via message edit
            await _pv1_show_review(context, chat_id, message_id, config)
        except ValueError:
            connector = config.get("connector_name", "")
            pair = config.get("trading_pair", "")
            amount = config.get("order_amount", "0.001")
            keyboard = [
                [
                    InlineKeyboardButton(
                        text="0.01%", callback_data="bots:pv1_spreads:0.0001"
                    ),
                    InlineKeyboardButton(
                        text="0.02%", callback_data="bots:pv1_spreads:0.0002"
                    ),
                    InlineKeyboardButton(
                        text="0.05%", callback_data="bots:pv1_spreads:0.0005"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="0.1%", callback_data="bots:pv1_spreads:0.001"
                    ),
                    InlineKeyboardButton(
                        text="0.2%", callback_data="bots:pv1_spreads:0.002"
                    ),
                    InlineKeyboardButton(
                        text="0.5%", callback_data="bots:pv1_spreads:0.005"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="« Back", callback_data="bots:pv1_back:amount"
                    ),
                    InlineKeyboardButton(
                        text="✕ Cancel", callback_data="bots:main_menu"
                    ),
                ],
            ]
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=r"*📈 PMM V1 \- New Config*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
                + "\n"
                f"💰 Amount: `{escape_markdown_v2(str(amount))}`" + "\n\n"
                r"*Step 4/5:* 📊 Spread" + "\n\n"
                r"⚠️ _Invalid value\. Enter a positive number \(e\.g\. 0\.001\)_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )

    elif step == "review":
        # Handle field: value editing in review
        if ":" in user_input:
            field, _, value = user_input.partition(":")
            field = field.strip().lower()
            value = value.strip()

            if field in config:
                # Parse booleans
                if value.lower() in ("true", "false"):
                    config[field] = value.lower() == "true"
                # Parse lists
                elif value.startswith("["):
                    try:
                        import ast

                        config[field] = ast.literal_eval(value)
                    except Exception:
                        config[field] = value
                # Try numeric
                else:
                    try:
                        if "." in value:
                            config[field] = float(value)
                        else:
                            config[field] = int(value)
                    except ValueError:
                        config[field] = value

                set_controller_config(context, config)

        await _pv1_show_review(context, chat_id, message_id, config)


async def _pv1_show_review(context, chat_id, message_id, config):
    """Helper to show review step with copyable config format"""
    # Generate ID if not set
    if not config.get("id"):
        existing = context.user_data.get("controller_configs_list", [])
        config["id"] = pv1_generate_id(config, existing)
        set_controller_config(context, config)

    config_block = (
        f"id: {config.get('id', '')}\n"
        f"connector_name: {config.get('connector_name', '')}\n"
        f"trading_pair: {config.get('trading_pair', '')}\n"
        f"order_amount: {config.get('order_amount', '0.001')}\n"
        f"buy_spreads: {config.get('buy_spreads', [0.0002])}\n"
        f"sell_spreads: {config.get('sell_spreads', [0.0002])}\n"
        f"order_refresh_time: {config.get('order_refresh_time', 30)}\n"
        f"order_refresh_tolerance_pct: {config.get('order_refresh_tolerance_pct', '-1')}\n"
        f"filled_order_delay: {config.get('filled_order_delay', 60)}\n"
        f"inventory_skew_enabled: {config.get('inventory_skew_enabled', False)}\n"
        f"inventory_range_multiplier: {config.get('inventory_range_multiplier', '1.0')}\n"
        f"price_ceiling: {config.get('price_ceiling', '-1')}\n"
        f"price_floor: {config.get('price_floor', '-1')}\n"
        f"manual_kill_switch: {config.get('manual_kill_switch', False)}"
    )

    pair = config.get("trading_pair", "")
    message_text = (
        f"*{escape_markdown_v2(pair)}* \\- Review Config\n\n"
        f"```\n{config_block}\n```\n\n"
        f"_To edit, send `field: value` lines_"
    )

    keyboard = [
        [InlineKeyboardButton(text="✅ Save Config", callback_data="bots:pv1_save")],
        [
            InlineKeyboardButton(text="« Back", callback_data="bots:pv1_back:spreads"),
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
        if not is_not_modified(e):
            raise
