"""PMM Mister wizard."""

import logging

from condor.telegram.errors import is_not_modified
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from handlers.cex._shared import (
    get_cex_balances,
    get_correct_pair_format,
    get_trading_rules,
    validate_trading_pair,
)
from utils.telegram_formatters import escape_markdown_v2

from .._shared import (
    ORDER_TYPE_LABELS,
    get_bots_client,
    get_controller_config,
    init_new_controller_config,
    set_controller_config,
)
from ..controllers.pmm_mister import generate_id as pmm_generate_id
from ..controllers.pmm_mister import validate_config as pmm_validate_config
from ._wizard_common import (
    WizardSpec,
    save_config,
    show_connector_step,
    show_pair_step,
    show_pair_suggestions,
)

PMM_WIZARD = WizardSpec(
    key="pmm",
    title=r"PMM Mister",
    log_name="PMM",
    connector_action="bots:pmm_connector:",
    pair_action="bots:pmm_pair:",
    pair_select_action="bots:pmm_pair_select:",
    back_to_connector="bots:pmm_back:connector",
    review_back="bots:pmm_review_back",
    create_another="bots:new_pmm_mister",
)

logger = logging.getLogger(__name__)


async def show_new_pmm_mister_form(update: Update, context: Context) -> None:
    """Start the progressive PMM Mister wizard - Step 1: Connector"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.list_controller_configs()
        context.user_data["controller_configs_list"] = configs
    except Exception as e:
        logger.warning(f"Could not fetch existing configs: {e}")

    config = init_new_controller_config(context, "pmm_mister")
    context.user_data["bots_state"] = "pmm_wizard"
    context.user_data["pmm_wizard_step"] = "connector_name"
    context.user_data["pmm_wizard_message_id"] = query.message.message_id
    context.user_data["pmm_wizard_chat_id"] = query.message.chat.id

    await _show_pmm_wizard_connector_step(update, context)


async def _show_pmm_wizard_connector_step(update: Update, context: Context) -> None:
    """PMM Wizard Step 1: Select Connector"""
    await show_connector_step(
        update,
        context,
        PMM_WIZARD,
        r"*📈 PMM Mister \- New Config*" + "\n\n" r"*Step 1/8:* 🏦 Select Connector",
    )


async def handle_pmm_wizard_connector(
    update: Update, context: Context, connector: str
) -> None:
    """Handle connector selection"""
    config = get_controller_config(context)
    config["connector_name"] = connector
    set_controller_config(context, config)
    context.user_data["pmm_wizard_step"] = "trading_pair"
    await _show_pmm_wizard_pair_step(update, context)


async def _show_pmm_wizard_pair_step(update: Update, context: Context) -> None:
    """PMM Wizard Step 2: Trading Pair"""

    def prompt(connector: str, recent_pairs: list[str]) -> str:
        return (
            r"*📈 PMM Mister \- New Config*" + "\n\n"
            f"*Connector:* `{escape_markdown_v2(connector)}`" + "\n\n"
            r"*Step 2/8:* 🔗 Trading Pair" + "\n\n"
            r"Select or type a pair:"
        )

    await show_pair_step(update, context, PMM_WIZARD, prompt)


async def handle_pmm_wizard_pair(update: Update, context: Context, pair: str) -> None:
    """Handle pair selection"""
    config = get_controller_config(context)
    config["trading_pair"] = pair.upper()
    set_controller_config(context, config)

    connector = config.get("connector_name", "")

    # Only ask for leverage on perpetual exchanges
    if connector.endswith("_perpetual"):
        context.user_data["pmm_wizard_step"] = "leverage"
        await _show_pmm_wizard_leverage_step(update, context)
    else:
        # Spot exchange - set leverage to 1 and skip to allocation
        config["leverage"] = 1
        set_controller_config(context, config)
        context.user_data["pmm_wizard_step"] = "portfolio_allocation"
        await _show_pmm_wizard_allocation_step(update, context)


async def _show_pmm_wizard_leverage_step(update: Update, context: Context) -> None:
    """PMM Wizard Step 3: Leverage"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")

    keyboard = [
        [
            InlineKeyboardButton(text="1x", callback_data="bots:pmm_leverage:1"),
            InlineKeyboardButton(text="5x", callback_data="bots:pmm_leverage:5"),
            InlineKeyboardButton(text="10x", callback_data="bots:pmm_leverage:10"),
        ],
        [
            InlineKeyboardButton(text="20x", callback_data="bots:pmm_leverage:20"),
            InlineKeyboardButton(text="50x", callback_data="bots:pmm_leverage:50"),
            InlineKeyboardButton(text="75x", callback_data="bots:pmm_leverage:75"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Back", callback_data="bots:pmm_back:pair"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await query.message.edit_text(
        r"*📈 PMM Mister \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n\n"
        r"*Step 3/8:* ⚡ Leverage",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_pmm_wizard_leverage(
    update: Update, context: Context, leverage: int
) -> None:
    """Handle leverage selection"""
    config = get_controller_config(context)
    config["leverage"] = leverage
    set_controller_config(context, config)
    context.user_data["pmm_wizard_step"] = "portfolio_allocation"
    await _show_pmm_wizard_allocation_step(update, context)


async def _show_pmm_wizard_allocation_step(update: Update, context: Context) -> None:
    """PMM Wizard Step 4: Portfolio Allocation"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 20)

    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = "portfolio_allocation"

    # Back goes to leverage for perpetual, or pair for spot
    back_target = "leverage" if connector.endswith("_perpetual") else "pair"

    keyboard = [
        [
            InlineKeyboardButton(text="1%", callback_data="bots:pmm_alloc:0.01"),
            InlineKeyboardButton(text="2%", callback_data="bots:pmm_alloc:0.02"),
            InlineKeyboardButton(text="3%", callback_data="bots:pmm_alloc:0.03"),
        ],
        [
            InlineKeyboardButton(text="5%", callback_data="bots:pmm_alloc:0.05"),
            InlineKeyboardButton(text="10%", callback_data="bots:pmm_alloc:0.1"),
            InlineKeyboardButton(text="20%", callback_data="bots:pmm_alloc:0.2"),
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Back", callback_data=f"bots:pmm_back:{back_target}"
            ),
            InlineKeyboardButton(text="❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await query.message.edit_text(
        r"*📈 PMM Mister \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n"
        f"⚡ `{leverage}x`" + "\n\n"
        r"*Step 4/8:* 💰 Portfolio Allocation" + "\n\n"
        r"_Or type a custom value \(e\.g\. 3% or 0\.03\)_",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_pmm_wizard_allocation(
    update: Update, context: Context, allocation: float
) -> None:
    """Handle allocation selection"""
    config = get_controller_config(context)
    config["portfolio_allocation"] = allocation
    set_controller_config(context, config)
    context.user_data["pmm_wizard_step"] = "total_amount_quote"
    await _show_pmm_wizard_amount_step(update, context)


async def _show_pmm_wizard_amount_step(update: Update, context: Context) -> None:
    """PMM Wizard Step 5: Total Amount Quote"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 20)
    allocation = config.get("portfolio_allocation", 0.05)

    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = "total_amount_quote"

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
        connector_balances = []
        connector_lower = connector.lower()
        connector_base = connector_lower.replace("_perpetual", "").replace("_spot", "")

        for bal_connector, bal_list in balances.items():
            bal_lower = bal_connector.lower()
            bal_base = bal_lower.replace("_perpetual", "").replace("_spot", "")
            if bal_lower == connector_lower or bal_base == connector_base:
                connector_balances = bal_list
                break

        if connector_balances:
            relevant_balances = []
            for bal in connector_balances:
                token = bal.get("token", bal.get("asset", ""))
                available = bal.get(
                    "units", bal.get("available_balance", bal.get("free", 0))
                )
                value_usd = bal.get("value", 0)
                if token and available:
                    try:
                        available_float = float(available)
                        if available_float > 0:
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
                    if available >= 1000:
                        amt_str = f"{available:,.0f}"
                    elif available >= 1:
                        amt_str = f"{available:,.2f}"
                    else:
                        amt_str = f"{available:,.6f}"

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
                balance_text = f"_No {escape_markdown_v2(quote_token)} balance on {escape_markdown_v2(connector)}_\n\n"
        elif balances:
            balance_text = f"_No {escape_markdown_v2(quote_token)} balance found_\n\n"
    except Exception as e:
        logger.warning(f"Could not fetch balances for PMM amount step: {e}")

    keyboard = [
        [
            InlineKeyboardButton(text="💵 100", callback_data="bots:pmm_amount:100"),
            InlineKeyboardButton(text="💵 500", callback_data="bots:pmm_amount:500"),
            InlineKeyboardButton(text="💵 1000", callback_data="bots:pmm_amount:1000"),
        ],
        [
            InlineKeyboardButton(text="💰 2000", callback_data="bots:pmm_amount:2000"),
            InlineKeyboardButton(text="💰 5000", callback_data="bots:pmm_amount:5000"),
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Back", callback_data="bots:pmm_back:allocation"
            ),
            InlineKeyboardButton(text="❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    message_text = (
        r"*📈 PMM Mister \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n"
        f"⚡ `{leverage}x` \\| 💰 `{allocation*100:.1f}%`"
        + "\n\n"
        + balance_text
        + r"*Step 5/8:* 💵 Total Amount \(Quote\)"
        + "\n\n"
        r"Select or type amount:"
    )

    try:
        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
    except Exception:
        pass


async def handle_pmm_wizard_amount(
    update: Update, context: Context, amount: float
) -> None:
    """Handle amount selection in PMM wizard"""
    config = get_controller_config(context)
    config["total_amount_quote"] = amount
    set_controller_config(context, config)
    context.user_data["pmm_wizard_step"] = "spreads"
    await _show_pmm_wizard_spreads_step(update, context)


async def _show_pmm_wizard_spreads_step(update: Update, context: Context) -> None:
    """PMM Wizard Step 6: Spreads"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 20)
    allocation = config.get("portfolio_allocation", 0.05)

    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = "spreads"

    amount = config.get("total_amount_quote", 100)

    keyboard = [
        [
            InlineKeyboardButton(
                text="Tight: 0.02%, 0.1%", callback_data="bots:pmm_spreads:0.0002,0.001"
            )
        ],
        [
            InlineKeyboardButton(
                text="Normal: 0.5%, 1%", callback_data="bots:pmm_spreads:0.005,0.01"
            )
        ],
        [
            InlineKeyboardButton(
                text="Wide: 1%, 2%", callback_data="bots:pmm_spreads:0.01,0.02"
            )
        ],
        [
            InlineKeyboardButton(text="⬅️ Back", callback_data="bots:pmm_back:amount"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await query.message.edit_text(
        r"*📈 PMM Mister \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n"
        f"⚡ `{leverage}x` \\| 💰 `{allocation*100:.0f}%` \\| 💵 `{amount:,.0f}`"
        + "\n\n"
        r"*Step 6/8:* 📊 Spreads" + "\n\n"
        r"_Or type custom: `0\.01,0\.02`_",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def _show_pmm_wizard_spreads_step_msg(
    context: Context, chat_id: int, message_id: int, config: dict
) -> None:
    """Show spreads step via direct message edit (for text input flow)"""
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 20)
    allocation = config.get("portfolio_allocation", 0.05)
    amount = config.get("total_amount_quote", 100)

    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = "spreads"

    keyboard = [
        [
            InlineKeyboardButton(
                text="Tight: 0.02%, 0.1%", callback_data="bots:pmm_spreads:0.0002,0.001"
            )
        ],
        [
            InlineKeyboardButton(
                text="Normal: 0.5%, 1%", callback_data="bots:pmm_spreads:0.005,0.01"
            )
        ],
        [
            InlineKeyboardButton(
                text="Wide: 1%, 2%", callback_data="bots:pmm_spreads:0.01,0.02"
            )
        ],
        [
            InlineKeyboardButton(text="⬅️ Back", callback_data="bots:pmm_back:amount"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=r"*📈 PMM Mister \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n"
        f"⚡ `{leverage}x` \\| 💰 `{allocation*100:.0f}%` \\| 💵 `{amount:,.0f}`"
        + "\n\n"
        r"*Step 6/8:* 📊 Spreads" + "\n\n"
        r"_Or type custom: `0\.01,0\.02`_",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def _show_pmm_wizard_amount_step_msg(
    context: Context, chat_id: int, message_id: int, config: dict
) -> None:
    """Show amount step via direct message edit (for text input flow)"""
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 20)
    allocation = config.get("portfolio_allocation", 0.05)

    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = "total_amount_quote"

    keyboard = [
        [
            InlineKeyboardButton(text="💵 100", callback_data="bots:pmm_amount:100"),
            InlineKeyboardButton(text="💵 500", callback_data="bots:pmm_amount:500"),
            InlineKeyboardButton(text="💵 1000", callback_data="bots:pmm_amount:1000"),
        ],
        [
            InlineKeyboardButton(text="💰 2000", callback_data="bots:pmm_amount:2000"),
            InlineKeyboardButton(text="💰 5000", callback_data="bots:pmm_amount:5000"),
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Back", callback_data="bots:pmm_back:allocation"
            ),
            InlineKeyboardButton(text="❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=r"*📈 PMM Mister \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n"
        f"⚡ `{leverage}x` \\| 💰 `{allocation*100:.1f}%`" + "\n\n"
        r"*Step 5/8:* 💵 Total Amount \(Quote\)" + "\n\n"
        r"Select or type amount:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_pmm_wizard_spreads(
    update: Update, context: Context, spreads: str
) -> None:
    """Handle spreads selection"""
    config = get_controller_config(context)
    config["buy_spreads"] = spreads
    config["sell_spreads"] = spreads
    set_controller_config(context, config)
    context.user_data["pmm_wizard_step"] = "take_profit"
    await _show_pmm_wizard_tp_step(update, context)


async def _show_pmm_wizard_tp_step(update: Update, context: Context) -> None:
    """PMM Wizard Step 7: Take Profit"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 20)
    allocation = config.get("portfolio_allocation", 0.05)
    spreads = config.get("buy_spreads", "0.0002,0.001")

    keyboard = [
        [
            InlineKeyboardButton(text="0.01%", callback_data="bots:pmm_tp:0.0001"),
            InlineKeyboardButton(text="0.02%", callback_data="bots:pmm_tp:0.0002"),
            InlineKeyboardButton(text="0.05%", callback_data="bots:pmm_tp:0.0005"),
        ],
        [
            InlineKeyboardButton(text="0.1%", callback_data="bots:pmm_tp:0.001"),
            InlineKeyboardButton(text="0.2%", callback_data="bots:pmm_tp:0.002"),
            InlineKeyboardButton(text="0.5%", callback_data="bots:pmm_tp:0.005"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Back", callback_data="bots:pmm_back:spreads"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await query.message.edit_text(
        r"*📈 PMM Mister \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n"
        f"⚡ `{leverage}x` \\| 💰 `{allocation*100:.0f}%`" + "\n"
        f"📊 Spreads: `{escape_markdown_v2(spreads)}`" + "\n\n"
        r"*Step 7/8:* 🎯 Take Profit",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_pmm_wizard_tp(update: Update, context: Context, tp: float) -> None:
    """Handle take profit selection"""
    config = get_controller_config(context)
    config["take_profit"] = tp
    set_controller_config(context, config)
    context.user_data["pmm_wizard_step"] = "review"
    await _show_pmm_wizard_review_step(update, context)


async def _show_pmm_wizard_review_step(update: Update, context: Context) -> None:
    """PMM Wizard Step 7: Review with copyable config format"""
    query = update.callback_query
    config = get_controller_config(context)

    # Generate ID if not set
    if not config.get("id"):
        existing = context.user_data.get("controller_configs_list", [])
        config["id"] = pmm_generate_id(config, existing)
        set_controller_config(context, config)

    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = "review"

    # Format order types as string
    tp_order_type = config.get("take_profit_order_type", "LIMIT_MAKER")
    if isinstance(tp_order_type, int):
        tp_order_type_str = ORDER_TYPE_LABELS.get(tp_order_type, "LIMIT_MAKER")
    else:
        tp_order_type_str = str(tp_order_type)

    open_order_type = config.get("open_order_type", "LIMIT")
    if isinstance(open_order_type, int):
        open_order_type_str = ORDER_TYPE_LABELS.get(open_order_type, "LIMIT")
    else:
        open_order_type_str = str(open_order_type)

    # Get amounts
    buy_spreads = config.get("buy_spreads", "0.0002,0.0006")
    sell_spreads = config.get("sell_spreads", "0.0002,0.0006")
    buy_amounts = config.get("buy_amounts_pct", "1,1")
    sell_amounts = config.get("sell_amounts_pct", "1,1")

    if not buy_amounts:
        num_buy_spreads = (
            len(buy_spreads.split(","))
            if isinstance(buy_spreads, str) and buy_spreads
            else 1
        )
        buy_amounts = ",".join(["1"] * num_buy_spreads)
    if not sell_amounts:
        num_sell_spreads = (
            len(sell_spreads.split(","))
            if isinstance(sell_spreads, str) and sell_spreads
            else 1
        )
        sell_amounts = ",".join(["1"] * num_sell_spreads)

    # Build copyable config block
    config_block = (
        f"id: {config.get('id', '')}\n"
        f"connector_name: {config.get('connector_name', '')}\n"
        f"trading_pair: {config.get('trading_pair', '')}\n"
        f"leverage: {config.get('leverage', 1)}\n"
        f"position_mode: {config.get('position_mode', 'ONEWAY')}\n"
        f"total_amount_quote: {config.get('total_amount_quote', 1000)}\n"
        f"portfolio_allocation: {config.get('portfolio_allocation', 0.03)}\n"
        f"target_base_pct: {config.get('target_base_pct', 0.5)}\n"
        f"min_base_pct: {config.get('min_base_pct', 0.3)}\n"
        f"max_base_pct: {config.get('max_base_pct', 0.7)}\n"
        f"buy_spreads: {buy_spreads}\n"
        f"sell_spreads: {sell_spreads}\n"
        f"buy_amounts_pct: {buy_amounts}\n"
        f"sell_amounts_pct: {sell_amounts}\n"
        f"take_profit: {config.get('take_profit', 0.0003)}\n"
        f"take_profit_order_type: {tp_order_type_str}\n"
        f"open_order_type: {open_order_type_str}\n"
        f"executor_refresh_time: {config.get('executor_refresh_time', 10)}\n"
        f"buy_cooldown_time: {config.get('buy_cooldown_time', 10)}\n"
        f"sell_cooldown_time: {config.get('sell_cooldown_time', 10)}\n"
        f"buy_position_effectivization_time: {config.get('buy_position_effectivization_time', 300)}\n"
        f"sell_position_effectivization_time: {config.get('sell_position_effectivization_time', 300)}\n"
        f"price_distance_tolerance: {config.get('price_distance_tolerance', '0.0005')}\n"
        f"refresh_tolerance: {config.get('refresh_tolerance', '0.0005')}\n"
        f"tolerance_scaling: {config.get('tolerance_scaling', '1.2')}\n"
        f"max_active_executors_by_level: {config.get('max_active_executors_by_level', 10)}\n"
        f"position_profit_protection: {config.get('position_profit_protection', True)}\n"
        f"min_skew: {config.get('min_skew', '1.0')}\n"
        f"global_take_profit: {config.get('global_take_profit', '0.03')}\n"
        f"global_stop_loss: {config.get('global_stop_loss', '0.05')}"
    )

    pair = config.get("trading_pair", "")
    message_text = (
        f"*{escape_markdown_v2(pair)}* \\- Review Config\n\n"
        f"```\n{config_block}\n```\n\n"
        f"_To edit, send `field: value` lines_"
    )

    keyboard = [
        [InlineKeyboardButton(text="✅ Save Config", callback_data="bots:pmm_save")],
        [
            InlineKeyboardButton(text="⬅️ Back", callback_data="bots:pmm_back:tp"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_pmm_back(update: Update, context: Context, target: str) -> None:
    """Handle back navigation in PMM wizard"""
    query = update.callback_query

    if target == "connector":
        await _show_pmm_wizard_connector_step(update, context)
    elif target == "pair":
        await _show_pmm_wizard_pair_step(update, context)
    elif target == "leverage":
        await _show_pmm_wizard_leverage_step(update, context)
    elif target == "allocation":
        await _show_pmm_wizard_allocation_step(update, context)
    elif target == "amount":
        await _show_pmm_wizard_amount_step(update, context)
    elif target == "spreads":
        await _show_pmm_wizard_spreads_step(update, context)
    elif target == "tp":
        await _show_pmm_wizard_tp_step(update, context)


async def handle_pmm_save(update: Update, context: Context) -> None:
    """Save PMM config"""
    await save_config(update, context, PMM_WIZARD, pmm_validate_config)


async def handle_pmm_review_back(update: Update, context: Context) -> None:
    """Back to review"""
    await _show_pmm_wizard_review_step(update, context)


async def handle_pmm_edit_id(update: Update, context: Context) -> None:
    """Edit config ID"""
    query = update.callback_query
    config = get_controller_config(context)
    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = "edit_id"

    keyboard = [
        [InlineKeyboardButton(text="❌ Cancel", callback_data="bots:pmm_review_back")]
    ]
    await query.message.edit_text(
        r"*Edit Config ID*" + "\n\n"
        f"Current: `{escape_markdown_v2(config.get('id', ''))}`" + "\n\n"
        r"Enter new ID:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_pmm_edit_field(update: Update, context: Context, field: str) -> None:
    """Handle editing a specific field from review"""
    query = update.callback_query
    config = get_controller_config(context)
    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = f"edit_{field}"

    keyboard = [
        [InlineKeyboardButton(text="❌ Cancel", callback_data="bots:pmm_review_back")]
    ]

    if field == "leverage":
        # Show leverage buttons instead of text input
        keyboard = [
            [
                InlineKeyboardButton(
                    text="1x", callback_data="bots:pmm_set:leverage:1"
                ),
                InlineKeyboardButton(
                    text="5x", callback_data="bots:pmm_set:leverage:5"
                ),
                InlineKeyboardButton(
                    text="10x", callback_data="bots:pmm_set:leverage:10"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="20x", callback_data="bots:pmm_set:leverage:20"
                ),
                InlineKeyboardButton(
                    text="50x", callback_data="bots:pmm_set:leverage:50"
                ),
                InlineKeyboardButton(
                    text="75x", callback_data="bots:pmm_set:leverage:75"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Cancel", callback_data="bots:pmm_review_back"
                )
            ],
        ]
        await query.message.edit_text(
            r"*Edit Leverage*" + "\n\n" f"Current: `{config.get('leverage', 20)}x`",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )

    elif field == "allocation":
        keyboard = [
            [
                InlineKeyboardButton(
                    text="1%", callback_data="bots:pmm_set:allocation:0.01"
                ),
                InlineKeyboardButton(
                    text="2%", callback_data="bots:pmm_set:allocation:0.02"
                ),
                InlineKeyboardButton(
                    text="3%", callback_data="bots:pmm_set:allocation:0.03"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="5%", callback_data="bots:pmm_set:allocation:0.05"
                ),
                InlineKeyboardButton(
                    text="10%", callback_data="bots:pmm_set:allocation:0.1"
                ),
                InlineKeyboardButton(
                    text="20%", callback_data="bots:pmm_set:allocation:0.2"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Cancel", callback_data="bots:pmm_review_back"
                )
            ],
        ]
        await query.message.edit_text(
            r"*Edit Portfolio Allocation*" + "\n\n"
            f"Current: `{config.get('portfolio_allocation', 0.05)*100:.0f}%`" + "\n\n"
            r"_Or type a custom value \(e\.g\. 3% or 0\.03\)_",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )

    elif field == "spreads":
        keyboard = [
            [
                InlineKeyboardButton(
                    text="Tight: 0.02%, 0.1%",
                    callback_data="bots:pmm_set:spreads:0.0002,0.001",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Normal: 0.5%, 1%",
                    callback_data="bots:pmm_set:spreads:0.005,0.01",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Wide: 1%, 2%", callback_data="bots:pmm_set:spreads:0.01,0.02"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Cancel", callback_data="bots:pmm_review_back"
                )
            ],
        ]
        await query.message.edit_text(
            r"*Edit Spreads*" + "\n\n"
            f"Buy: `{escape_markdown_v2(config.get('buy_spreads', ''))}`" + "\n"
            f"Sell: `{escape_markdown_v2(config.get('sell_spreads', ''))}`" + "\n\n"
            r"_Or type custom spreads \(e\.g\. 0\.001,0\.002\)_",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )

    elif field == "take_profit":
        keyboard = [
            [
                InlineKeyboardButton(
                    text="0.01%", callback_data="bots:pmm_set:take_profit:0.0001"
                ),
                InlineKeyboardButton(
                    text="0.02%", callback_data="bots:pmm_set:take_profit:0.0002"
                ),
                InlineKeyboardButton(
                    text="0.05%", callback_data="bots:pmm_set:take_profit:0.0005"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="0.1%", callback_data="bots:pmm_set:take_profit:0.001"
                ),
                InlineKeyboardButton(
                    text="0.2%", callback_data="bots:pmm_set:take_profit:0.002"
                ),
                InlineKeyboardButton(
                    text="0.5%", callback_data="bots:pmm_set:take_profit:0.005"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Cancel", callback_data="bots:pmm_review_back"
                )
            ],
        ]
        await query.message.edit_text(
            r"*Edit Take Profit*" + "\n\n"
            f"Current: `{config.get('take_profit', 0.0001)*100:.2f}%`" + "\n\n"
            r"_Or type a custom value \(e\.g\. 0\.001 for 0\.1%\)_",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )

    elif field == "base":
        await query.message.edit_text(
            r"*Edit Base Percentages*" + "\n\n"
            f"Min: `{config.get('min_base_pct', 0.1)*100:.0f}%`" + "\n"
            f"Target: `{config.get('target_base_pct', 0.2)*100:.0f}%`" + "\n"
            f"Max: `{config.get('max_base_pct', 0.4)*100:.0f}%`" + "\n\n"
            r"Enter new values \(min,target,max\):" + "\n"
            r"_Example: 0\.1,0\.2,0\.4_",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )


async def handle_pmm_set_field(
    update: Update, context: Context, field: str, value: str
) -> None:
    """Handle setting a field value from button click"""
    config = get_controller_config(context)

    if field == "leverage":
        config["leverage"] = int(value)
    elif field == "allocation":
        config["portfolio_allocation"] = float(value)
    elif field == "spreads":
        config["buy_spreads"] = value
        config["sell_spreads"] = value
    elif field == "take_profit":
        config["take_profit"] = float(value)

    set_controller_config(context, config)
    await _show_pmm_wizard_review_step(update, context)


async def handle_pmm_edit_advanced(update: Update, context: Context) -> None:
    """Show advanced settings"""
    query = update.callback_query
    config = get_controller_config(context)

    keyboard = [
        [
            InlineKeyboardButton(text="Base %", callback_data="bots:pmm_adv:base"),
            InlineKeyboardButton(
                text="Cooldowns", callback_data="bots:pmm_adv:cooldown"
            ),
        ],
        [
            InlineKeyboardButton(
                text="Refresh Time", callback_data="bots:pmm_adv:refresh"
            ),
            InlineKeyboardButton(
                text="Max Executors", callback_data="bots:pmm_adv:max_exec"
            ),
        ],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="bots:pmm_review_back")],
    ]

    await query.message.edit_text(
        r"*Advanced Settings*" + "\n\n"
        f"📈 *Base %:* min=`{config.get('min_base_pct', 0.3)*100:.0f}%` "
        f"target=`{config.get('target_base_pct', 0.5)*100:.0f}%` "
        f"max=`{config.get('max_base_pct', 0.7)*100:.0f}%`" + "\n"
        f"⏱️ *Refresh:* `{config.get('executor_refresh_time', 10)}s`" + "\n"
        f"⏸️ *Cooldowns:* buy=`{config.get('buy_cooldown_time', 10)}s` "
        f"sell=`{config.get('sell_cooldown_time', 10)}s`" + "\n"
        f"🔢 *Max Executors:* `{config.get('max_active_executors_by_level', 10)}`"
        + "\n"
        f"🛡️ *Profit Protection:* `{config.get('position_profit_protection', True)}`"
        + "\n"
        f"📊 *Global TP/SL:* `{config.get('global_take_profit', '0.03')}`/`{config.get('global_stop_loss', '0.05')}`",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_pmm_adv_setting(
    update: Update, context: Context, setting: str
) -> None:
    """Handle advanced setting edit"""
    query = update.callback_query
    config = get_controller_config(context)

    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = f"adv_{setting}"

    hints = {
        "base": (
            "Base Percentages",
            f"min={config.get('min_base_pct', 0.1)}, target={config.get('target_base_pct', 0.2)}, max={config.get('max_base_pct', 0.4)}",
            "min,target,max as decimals",
        ),
        "cooldown": (
            "Cooldown Times",
            f"buy={config.get('buy_cooldown_time', 15)}s, sell={config.get('sell_cooldown_time', 15)}s",
            "buy,sell in seconds",
        ),
        "refresh": (
            "Refresh Time",
            f"{config.get('executor_refresh_time', 30)}s",
            "seconds",
        ),
        "max_exec": (
            "Max Executors",
            str(config.get("max_active_executors_by_level", 4)),
            "number",
        ),
    }
    label, current, hint = hints.get(setting, (setting, "", ""))

    keyboard = [
        [InlineKeyboardButton(text="❌ Cancel", callback_data="bots:pmm_edit_advanced")]
    ]
    await query.message.edit_text(
        f"*Edit {escape_markdown_v2(label)}*" + "\n\n"
        f"Current: `{escape_markdown_v2(current)}`" + "\n\n"
        f"Enter new value \\({escape_markdown_v2(hint)}\\):",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def _show_pmm_pair_suggestions(
    update: Update,
    context: Context,
    input_pair: str,
    error_msg: str,
    suggestions: list,
    connector: str,
) -> None:
    """Show trading pair suggestions when validation fails in PMM wizard"""
    await show_pair_suggestions(update, context, PMM_WIZARD, error_msg, suggestions)


async def handle_pmm_pair_select(
    update: Update, context: Context, trading_pair: str
) -> None:
    """Handle selection of a suggested trading pair in PMM wizard"""
    config = get_controller_config(context)
    message_id = context.user_data.get("pmm_wizard_message_id")
    chat_id = context.user_data.get("pmm_wizard_chat_id")

    config["trading_pair"] = trading_pair
    set_controller_config(context, config)
    connector = config.get("connector_name", "")

    # Only ask for leverage on perpetual exchanges
    if connector.endswith("_perpetual"):
        context.user_data["pmm_wizard_step"] = "leverage"
        keyboard = [
            [
                InlineKeyboardButton(text="1x", callback_data="bots:pmm_leverage:1"),
                InlineKeyboardButton(text="5x", callback_data="bots:pmm_leverage:5"),
                InlineKeyboardButton(text="10x", callback_data="bots:pmm_leverage:10"),
            ],
            [
                InlineKeyboardButton(text="20x", callback_data="bots:pmm_leverage:20"),
                InlineKeyboardButton(text="50x", callback_data="bots:pmm_leverage:50"),
                InlineKeyboardButton(text="75x", callback_data="bots:pmm_leverage:75"),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Back", callback_data="bots:pmm_back:pair"
                ),
                InlineKeyboardButton(text="❌ Cancel", callback_data="bots:main_menu"),
            ],
        ]
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=r"*📈 PMM Mister \- New Config*" + "\n\n"
            f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(trading_pair)}`"
            + "\n\n"
            r"*Step 3/8:* ⚡ Leverage",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
    else:
        # Spot exchange - set leverage to 1 and skip to allocation
        config["leverage"] = 1
        set_controller_config(context, config)
        context.user_data["bots_state"] = "pmm_wizard_input"
        context.user_data["pmm_wizard_step"] = "portfolio_allocation"
        keyboard = [
            [
                InlineKeyboardButton(text="1%", callback_data="bots:pmm_alloc:0.01"),
                InlineKeyboardButton(text="2%", callback_data="bots:pmm_alloc:0.02"),
                InlineKeyboardButton(text="3%", callback_data="bots:pmm_alloc:0.03"),
            ],
            [
                InlineKeyboardButton(text="5%", callback_data="bots:pmm_alloc:0.05"),
                InlineKeyboardButton(text="10%", callback_data="bots:pmm_alloc:0.1"),
                InlineKeyboardButton(text="20%", callback_data="bots:pmm_alloc:0.2"),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Back", callback_data="bots:pmm_back:pair"
                ),
                InlineKeyboardButton(text="❌ Cancel", callback_data="bots:main_menu"),
            ],
        ]
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=r"*📈 PMM Mister \- New Config*" + "\n\n"
            f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(trading_pair)}`"
            + "\n\n"
            r"*Step 4/8:* 💰 Portfolio Allocation" + "\n\n"
            r"_Or type a custom value \(e\.g\. 3% or 0\.03\)_",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )


async def process_pmm_wizard_input(
    update: Update, context: Context, user_input: str
) -> None:
    """Process text input during PMM wizard"""
    step = context.user_data.get("pmm_wizard_step", "")
    config = get_controller_config(context)
    message_id = context.user_data.get("pmm_wizard_message_id")
    chat_id = context.user_data.get("pmm_wizard_chat_id")

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
            # Show error with suggestions
            await _show_pmm_pair_suggestions(
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

        config["trading_pair"] = pair
        set_controller_config(context, config)

        # Only ask for leverage on perpetual exchanges
        if connector.endswith("_perpetual"):
            context.user_data["pmm_wizard_step"] = "leverage"
            keyboard = [
                [
                    InlineKeyboardButton(
                        text="1x", callback_data="bots:pmm_leverage:1"
                    ),
                    InlineKeyboardButton(
                        text="5x", callback_data="bots:pmm_leverage:5"
                    ),
                    InlineKeyboardButton(
                        text="10x", callback_data="bots:pmm_leverage:10"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="20x", callback_data="bots:pmm_leverage:20"
                    ),
                    InlineKeyboardButton(
                        text="50x", callback_data="bots:pmm_leverage:50"
                    ),
                    InlineKeyboardButton(
                        text="75x", callback_data="bots:pmm_leverage:75"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Back", callback_data="bots:pmm_back:pair"
                    ),
                    InlineKeyboardButton(
                        text="❌ Cancel", callback_data="bots:main_menu"
                    ),
                ],
            ]
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=r"*📈 PMM Mister \- New Config*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(config['trading_pair'])}`"
                + "\n\n"
                r"*Step 3/8:* ⚡ Leverage",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )
        else:
            # Spot exchange - set leverage to 1 and skip to allocation
            config["leverage"] = 1
            set_controller_config(context, config)
            context.user_data["bots_state"] = "pmm_wizard_input"
            context.user_data["pmm_wizard_step"] = "portfolio_allocation"
            keyboard = [
                [
                    InlineKeyboardButton(
                        text="1%", callback_data="bots:pmm_alloc:0.01"
                    ),
                    InlineKeyboardButton(
                        text="2%", callback_data="bots:pmm_alloc:0.02"
                    ),
                    InlineKeyboardButton(
                        text="3%", callback_data="bots:pmm_alloc:0.03"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="5%", callback_data="bots:pmm_alloc:0.05"
                    ),
                    InlineKeyboardButton(
                        text="10%", callback_data="bots:pmm_alloc:0.1"
                    ),
                    InlineKeyboardButton(
                        text="20%", callback_data="bots:pmm_alloc:0.2"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Back", callback_data="bots:pmm_back:pair"
                    ),
                    InlineKeyboardButton(
                        text="❌ Cancel", callback_data="bots:main_menu"
                    ),
                ],
            ]
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=r"*📈 PMM Mister \- New Config*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(config['trading_pair'])}`"
                + "\n\n"
                r"*Step 4/8:* 💰 Portfolio Allocation" + "\n\n"
                r"_Or type a custom value \(e\.g\. 3% or 0\.03\)_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )

    elif step == "portfolio_allocation":
        # Parse allocation value (handle "3%" or "0.03" formats)
        try:
            val_str = user_input.strip().replace("%", "")
            val = float(val_str)
            if val > 1:  # User entered percentage like "3" or "3%"
                val = val / 100
            config["portfolio_allocation"] = val
            set_controller_config(context, config)
            context.user_data["pmm_wizard_step"] = "total_amount_quote"
            await _show_pmm_wizard_amount_step_msg(context, chat_id, message_id, config)
        except ValueError:
            # Invalid input - show error and keep at same step
            connector = config.get("connector_name", "")
            pair = config.get("trading_pair", "")
            leverage = config.get("leverage", 20)
            back_target = "leverage" if connector.endswith("_perpetual") else "pair"
            keyboard = [
                [
                    InlineKeyboardButton(
                        text="1%", callback_data="bots:pmm_alloc:0.01"
                    ),
                    InlineKeyboardButton(
                        text="2%", callback_data="bots:pmm_alloc:0.02"
                    ),
                    InlineKeyboardButton(
                        text="3%", callback_data="bots:pmm_alloc:0.03"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="5%", callback_data="bots:pmm_alloc:0.05"
                    ),
                    InlineKeyboardButton(
                        text="10%", callback_data="bots:pmm_alloc:0.1"
                    ),
                    InlineKeyboardButton(
                        text="20%", callback_data="bots:pmm_alloc:0.2"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Back", callback_data=f"bots:pmm_back:{back_target}"
                    ),
                    InlineKeyboardButton(
                        text="❌ Cancel", callback_data="bots:main_menu"
                    ),
                ],
            ]
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=r"*📈 PMM Mister \- New Config*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
                + "\n"
                f"⚡ `{leverage}x`" + "\n\n"
                r"*Step 4/8:* 💰 Portfolio Allocation" + "\n\n"
                r"⚠️ _Invalid value\. Enter a percentage \(e\.g\. 3% or 0\.03\)_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )

    elif step == "total_amount_quote":
        # Parse amount value
        try:
            amount = float(user_input.strip().replace(",", ""))
            config["total_amount_quote"] = amount
            set_controller_config(context, config)
            context.user_data["pmm_wizard_step"] = "spreads"
            await _show_pmm_wizard_spreads_step_msg(
                context, chat_id, message_id, config
            )
        except ValueError:
            # Invalid input - show error and keep at same step
            connector = config.get("connector_name", "")
            pair = config.get("trading_pair", "")
            leverage = config.get("leverage", 20)
            allocation = config.get("portfolio_allocation", 0.05)
            keyboard = [
                [
                    InlineKeyboardButton(
                        text="💵 100", callback_data="bots:pmm_amount:100"
                    ),
                    InlineKeyboardButton(
                        text="💵 500", callback_data="bots:pmm_amount:500"
                    ),
                    InlineKeyboardButton(
                        text="💵 1000", callback_data="bots:pmm_amount:1000"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="💰 2000", callback_data="bots:pmm_amount:2000"
                    ),
                    InlineKeyboardButton(
                        text="💰 5000", callback_data="bots:pmm_amount:5000"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Back", callback_data="bots:pmm_back:allocation"
                    ),
                    InlineKeyboardButton(
                        text="❌ Cancel", callback_data="bots:main_menu"
                    ),
                ],
            ]
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=r"*📈 PMM Mister \- New Config*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
                + "\n"
                f"⚡ `{leverage}x` \\| 💰 `{allocation*100:.1f}%`" + "\n\n"
                r"*Step 5/8:* 💵 Total Amount \(Quote\)" + "\n\n"
                r"⚠️ _Invalid value\. Enter a number \(e\.g\. 500\)_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )

    elif step == "spreads":
        config["buy_spreads"] = user_input.strip()
        config["sell_spreads"] = user_input.strip()
        set_controller_config(context, config)
        context.user_data["pmm_wizard_step"] = "take_profit"
        connector = config.get("connector_name", "")
        pair = config.get("trading_pair", "")
        leverage = config.get("leverage", 20)
        allocation = config.get("portfolio_allocation", 0.05)
        keyboard = [
            [
                InlineKeyboardButton(text="0.01%", callback_data="bots:pmm_tp:0.0001"),
                InlineKeyboardButton(text="0.02%", callback_data="bots:pmm_tp:0.0002"),
                InlineKeyboardButton(text="0.05%", callback_data="bots:pmm_tp:0.0005"),
            ],
            [
                InlineKeyboardButton(text="0.1%", callback_data="bots:pmm_tp:0.001"),
                InlineKeyboardButton(text="0.2%", callback_data="bots:pmm_tp:0.002"),
                InlineKeyboardButton(text="0.5%", callback_data="bots:pmm_tp:0.005"),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Back", callback_data="bots:pmm_back:spreads"
                ),
                InlineKeyboardButton(text="❌ Cancel", callback_data="bots:main_menu"),
            ],
        ]
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=r"*📈 PMM Mister \- New Config*" + "\n\n"
            f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
            + "\n"
            f"⚡ `{leverage}x` \\| 💰 `{allocation*100:.0f}%`" + "\n"
            f"📊 Spreads: `{escape_markdown_v2(user_input.strip())}`" + "\n\n"
            r"*Step 7/8:* 🎯 Take Profit",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )

    elif step == "edit_id":
        config["id"] = user_input.strip()
        set_controller_config(context, config)
        await _pmm_show_review(context, chat_id, message_id, config)

    elif step == "edit_allocation":
        try:
            val = float(user_input.strip())
            if val > 1:  # User entered percentage
                val = val / 100
            config["portfolio_allocation"] = val
            set_controller_config(context, config)
        except ValueError:
            pass
        await _pmm_show_review(context, chat_id, message_id, config)

    elif step == "edit_spreads":
        config["buy_spreads"] = user_input.strip()
        config["sell_spreads"] = user_input.strip()
        set_controller_config(context, config)
        await _pmm_show_review(context, chat_id, message_id, config)

    elif step == "edit_take_profit":
        try:
            val = float(user_input.strip())
            config["take_profit"] = val
            set_controller_config(context, config)
        except ValueError:
            pass
        await _pmm_show_review(context, chat_id, message_id, config)

    elif step == "edit_base":
        try:
            parts = [float(x.strip()) for x in user_input.split(",")]
            if len(parts) == 3:
                (
                    config["min_base_pct"],
                    config["target_base_pct"],
                    config["max_base_pct"],
                ) = parts
                set_controller_config(context, config)
        except ValueError:
            pass
        await _pmm_show_review(context, chat_id, message_id, config)

    elif step == "adv_base":
        try:
            parts = [float(x.strip()) for x in user_input.split(",")]
            if len(parts) == 3:
                (
                    config["min_base_pct"],
                    config["target_base_pct"],
                    config["max_base_pct"],
                ) = parts
                set_controller_config(context, config)
        except ValueError:
            pass
        await _pmm_show_advanced(context, chat_id, message_id, config)

    elif step == "adv_cooldown":
        try:
            parts = [int(x.strip()) for x in user_input.split(",")]
            if len(parts) == 2:
                config["buy_cooldown_time"], config["sell_cooldown_time"] = parts
                set_controller_config(context, config)
        except ValueError:
            pass
        await _pmm_show_advanced(context, chat_id, message_id, config)

    elif step == "adv_refresh":
        try:
            config["executor_refresh_time"] = int(user_input)
            set_controller_config(context, config)
        except ValueError:
            pass
        await _pmm_show_advanced(context, chat_id, message_id, config)

    elif step == "adv_max_exec":
        try:
            config["max_active_executors_by_level"] = int(user_input)
            set_controller_config(context, config)
        except ValueError:
            pass
        await _pmm_show_advanced(context, chat_id, message_id, config)

    elif step == "review":
        # Parse field: value or field=value pairs
        field_map = {
            "id": ("id", str),
            "connector_name": ("connector_name", str),
            "trading_pair": ("trading_pair", str),
            "leverage": ("leverage", int),
            "position_mode": ("position_mode", str),
            "total_amount_quote": ("total_amount_quote", float),
            "portfolio_allocation": ("portfolio_allocation", float),
            "target_base_pct": ("target_base_pct", float),
            "min_base_pct": ("min_base_pct", float),
            "max_base_pct": ("max_base_pct", float),
            "buy_spreads": ("buy_spreads", str),
            "sell_spreads": ("sell_spreads", str),
            "buy_amounts_pct": ("buy_amounts_pct", str),
            "sell_amounts_pct": ("sell_amounts_pct", str),
            "take_profit": ("take_profit", float),
            "take_profit_order_type": ("take_profit_order_type", str),
            "open_order_type": ("open_order_type", str),
            "executor_refresh_time": ("executor_refresh_time", int),
            "buy_cooldown_time": ("buy_cooldown_time", int),
            "sell_cooldown_time": ("sell_cooldown_time", int),
            "buy_position_effectivization_time": (
                "buy_position_effectivization_time",
                int,
            ),
            "sell_position_effectivization_time": (
                "sell_position_effectivization_time",
                int,
            ),
            "price_distance_tolerance": ("price_distance_tolerance", str),
            "refresh_tolerance": ("refresh_tolerance", str),
            "tolerance_scaling": ("tolerance_scaling", str),
            "max_active_executors_by_level": ("max_active_executors_by_level", int),
            "position_profit_protection": ("position_profit_protection", str),
            "min_skew": ("min_skew", str),
            "global_take_profit": ("global_take_profit", str),
            "global_stop_loss": ("global_stop_loss", str),
            "manual_kill_switch": ("manual_kill_switch", str),
        }

        updated_fields = []
        lines = user_input.strip().split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Parse field: value or field=value
            if ":" in line:
                parts = line.split(":", 1)
            elif "=" in line:
                parts = line.split("=", 1)
            else:
                continue

            if len(parts) != 2:
                continue

            field_name = parts[0].strip().lower()
            value_str = parts[1].strip()

            if field_name in field_map:
                config_key, type_fn = field_map[field_name]
                try:
                    # Special handling for order type fields
                    if config_key in ("take_profit_order_type", "open_order_type"):
                        # Normalize input to uppercase with underscores
                        normalized = value_str.upper().replace(" ", "_")
                        if normalized in ("LIMIT_MAKER", "LIMIT", "MARKET"):
                            config[config_key] = normalized
                            updated_fields.append(field_name)
                    elif type_fn == str:
                        config[config_key] = value_str
                        updated_fields.append(field_name)
                    else:
                        config[config_key] = type_fn(value_str)
                        updated_fields.append(field_name)
                except (ValueError, TypeError):
                    pass

        if updated_fields:
            set_controller_config(context, config)
            await _pmm_show_review(context, chat_id, message_id, config)


async def _pmm_show_review(context, chat_id, message_id, config):
    """Helper to show review step with copyable config format"""
    # Format order types as string
    tp_order_type = config.get("take_profit_order_type", "LIMIT_MAKER")
    if isinstance(tp_order_type, int):
        tp_order_type_str = ORDER_TYPE_LABELS.get(tp_order_type, "LIMIT_MAKER")
    else:
        tp_order_type_str = str(tp_order_type)

    open_order_type = config.get("open_order_type", "LIMIT_MAKER")
    if isinstance(open_order_type, int):
        open_order_type_str = ORDER_TYPE_LABELS.get(open_order_type, "LIMIT_MAKER")
    else:
        open_order_type_str = str(open_order_type)

    # Get amounts
    buy_spreads = config.get("buy_spreads", "0.0002,0.0006")
    sell_spreads = config.get("sell_spreads", "0.0002,0.0006")
    buy_amounts = config.get("buy_amounts_pct", "1,1")
    sell_amounts = config.get("sell_amounts_pct", "1,1")

    if not buy_amounts:
        num_buy_spreads = (
            len(buy_spreads.split(","))
            if isinstance(buy_spreads, str) and buy_spreads
            else 1
        )
        buy_amounts = ",".join(["1"] * num_buy_spreads)
    if not sell_amounts:
        num_sell_spreads = (
            len(sell_spreads.split(","))
            if isinstance(sell_spreads, str) and sell_spreads
            else 1
        )
        sell_amounts = ",".join(["1"] * num_sell_spreads)

    # Build copyable config block
    config_block = (
        f"id: {config.get('id', '')}\n"
        f"connector_name: {config.get('connector_name', '')}\n"
        f"trading_pair: {config.get('trading_pair', '')}\n"
        f"leverage: {config.get('leverage', 1)}\n"
        f"position_mode: {config.get('position_mode', 'ONEWAY')}\n"
        f"total_amount_quote: {config.get('total_amount_quote', 1000)}\n"
        f"portfolio_allocation: {config.get('portfolio_allocation', 0.03)}\n"
        f"target_base_pct: {config.get('target_base_pct', 0.5)}\n"
        f"min_base_pct: {config.get('min_base_pct', 0.3)}\n"
        f"max_base_pct: {config.get('max_base_pct', 0.7)}\n"
        f"buy_spreads: {buy_spreads}\n"
        f"sell_spreads: {sell_spreads}\n"
        f"buy_amounts_pct: {buy_amounts}\n"
        f"sell_amounts_pct: {sell_amounts}\n"
        f"take_profit: {config.get('take_profit', 0.0003)}\n"
        f"take_profit_order_type: {tp_order_type_str}\n"
        f"open_order_type: {open_order_type_str}\n"
        f"executor_refresh_time: {config.get('executor_refresh_time', 10)}\n"
        f"buy_cooldown_time: {config.get('buy_cooldown_time', 10)}\n"
        f"sell_cooldown_time: {config.get('sell_cooldown_time', 10)}\n"
        f"buy_position_effectivization_time: {config.get('buy_position_effectivization_time', 300)}\n"
        f"sell_position_effectivization_time: {config.get('sell_position_effectivization_time', 300)}\n"
        f"price_distance_tolerance: {config.get('price_distance_tolerance', '0.0005')}\n"
        f"refresh_tolerance: {config.get('refresh_tolerance', '0.0005')}\n"
        f"tolerance_scaling: {config.get('tolerance_scaling', '1.2')}\n"
        f"max_active_executors_by_level: {config.get('max_active_executors_by_level', 10)}\n"
        f"position_profit_protection: {config.get('position_profit_protection', True)}\n"
        f"min_skew: {config.get('min_skew', '1.0')}\n"
        f"global_take_profit: {config.get('global_take_profit', '0.03')}\n"
        f"global_stop_loss: {config.get('global_stop_loss', '0.05')}"
    )

    pair = config.get("trading_pair", "")
    message_text = (
        f"*{escape_markdown_v2(pair)}* \\- Review Config\n\n"
        f"```\n{config_block}\n```\n\n"
        f"_To edit, send `field: value` lines_"
    )

    keyboard = [
        [InlineKeyboardButton(text="✅ Save Config", callback_data="bots:pmm_save")],
        [
            InlineKeyboardButton(text="⬅️ Back", callback_data="bots:pmm_back:tp"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="bots:main_menu"),
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
        # Ignore "Message is not modified" error
        if not is_not_modified(e):
            raise


async def _pmm_show_advanced(context, chat_id, message_id, config):
    """Helper to show advanced settings"""
    keyboard = [
        [
            InlineKeyboardButton(text="Base %", callback_data="bots:pmm_adv:base"),
            InlineKeyboardButton(
                text="Cooldowns", callback_data="bots:pmm_adv:cooldown"
            ),
        ],
        [
            InlineKeyboardButton(
                text="Refresh Time", callback_data="bots:pmm_adv:refresh"
            ),
            InlineKeyboardButton(
                text="Max Executors", callback_data="bots:pmm_adv:max_exec"
            ),
        ],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="bots:pmm_review_back")],
    ]
    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=r"*Advanced Settings*" + "\n\n"
        f"📈 *Base %:* min=`{config.get('min_base_pct', 0.3)*100:.0f}%` "
        f"target=`{config.get('target_base_pct', 0.5)*100:.0f}%` "
        f"max=`{config.get('max_base_pct', 0.7)*100:.0f}%`" + "\n"
        f"⏱️ *Refresh:* `{config.get('executor_refresh_time', 10)}s`" + "\n"
        f"⏸️ *Cooldowns:* buy=`{config.get('buy_cooldown_time', 10)}s` "
        f"sell=`{config.get('sell_cooldown_time', 10)}s`" + "\n"
        f"🔢 *Max Executors:* `{config.get('max_active_executors_by_level', 10)}`"
        + "\n"
        f"🛡️ *Profit Protection:* `{config.get('position_profit_protection', True)}`"
        + "\n"
        f"📊 *Global TP/SL:* `{config.get('global_take_profit', '0.03')}`/`{config.get('global_stop_loss', '0.05')}`",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
