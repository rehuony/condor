"""Controller configs menu: list, multi-select, delete, branch, deploy entry."""

import asyncio
import logging

from condor.telegram.errors import BadRequest, is_not_modified
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from utils.telegram_formatters import escape_markdown_v2, format_error_message

from .._shared import (
    get_bots_client,
)
from ..controllers import get_controller_info, get_supported_controller_types
from .deploy_progressive import show_deploy_configure

logger = logging.getLogger(__name__)


# Pagination settings for configs
CONFIGS_PER_PAGE = 8  # Reduced to leave space for action buttons


def _get_controller_type_display(controller_name: str) -> tuple[str, str]:
    """Get display name and emoji for controller type"""
    type_map = {
        "grid_strike": ("Grid Strike", "📊"),
        "pmm_mister": ("PMM Mister", "📈"),
        "pmm_v1": ("PMM V1", "📈"),
        "dman_v3": ("DMan V3", "🤖"),
        "xemm": ("XEMM", "🔄"),
        "pmm": ("PMM", "📈"),
    }
    controller_lower = controller_name.lower() if controller_name else ""
    for key, (name, emoji) in type_map.items():
        if key in controller_lower:
            return name, emoji
    return controller_name or "Unknown", "⚙️"


def _format_config_line(cfg: dict, index: int) -> str:
    """Format a single config line with relevant info"""
    connector = cfg.get("connector_name", "")
    pair = cfg.get("trading_pair", "")
    side_val = cfg.get("side", 1)
    side = "L" if side_val == 1 else "S"
    start_price = cfg.get("start_price", 0)
    end_price = cfg.get("end_price", 0)

    # Build display: connector PAIR side [start-end]
    if connector and pair:
        # Format prices compactly
        if start_price and end_price:
            price_range = f"[{start_price:g}-{end_price:g}]"
        else:
            price_range = ""
        display = f"{connector} {pair} {side} {price_range}".strip()
    else:
        # Fallback to config ID
        config_id = cfg.get("id", "unnamed")
        display = config_id

    return f"{index}. {display}"


def _get_config_seq_num(cfg: dict) -> int:
    """Extract sequence number from config ID for sorting"""
    config_id = cfg.get("id", "")
    parts = config_id.split("_", 1)
    if parts and parts[0].isdigit():
        return int(parts[0])
    return -1  # No number goes to end


def _get_available_controller_types(configs: list) -> dict[str, int]:
    """Get available controller types with counts"""
    type_counts: dict[str, int] = {}
    for cfg in configs:
        ctrl_type = cfg.get("controller_name", "unknown")
        type_counts[ctrl_type] = type_counts.get(ctrl_type, 0) + 1
    return type_counts


def _get_selected_config_ids(context, type_configs: list) -> list[str]:
    """Get list of selected config IDs from selection state"""
    selected = context.user_data.get("selected_configs", {})  # {config_id: True}
    result = []
    for cfg in type_configs:
        cfg_id = cfg.get("id", "")
        if cfg_id and selected.get(cfg_id):
            result.append(cfg_id)
    return result


async def show_controller_configs_menu(
    update: Update, context: Context, page: int = 0
) -> None:
    """
    Unified configs menu - shows configs directly with type selector, multi-select,
    and actions (Deploy, Edit, Delete).

    Selection persists across type/page changes using config IDs (not indices).
    """
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.list_controller_configs()

        # Store all configs
        context.user_data["controller_configs_list"] = configs

        # Get available types from registry (always shows all supported types)
        all_types = get_supported_controller_types()
        type_counts = _get_available_controller_types(configs)

        # Determine current type (default to first registered or grid_strike)
        current_type = context.user_data.get("configs_controller_type")
        if not current_type or current_type not in all_types:
            current_type = all_types[0] if all_types else "grid_strike"
        context.user_data["configs_controller_type"] = current_type

        # Filter and sort configs by current type
        type_configs = [c for c in configs if c.get("controller_name") == current_type]
        type_configs.sort(key=_get_config_seq_num, reverse=True)
        context.user_data["configs_type_filtered"] = type_configs
        context.user_data["configs_page"] = page

        # Get selection state (uses config IDs for persistence)
        # Sync with available configs - remove any IDs that no longer exist
        selected = context.user_data.get("selected_configs", {})  # {config_id: True}
        available_ids = {c.get("id") for c in configs if c.get("id")}
        selected = {
            cfg_id: is_sel
            for cfg_id, is_sel in selected.items()
            if cfg_id in available_ids
        }
        context.user_data["selected_configs"] = selected
        selected_ids = [cfg_id for cfg_id, is_sel in selected.items() if is_sel]

        # Calculate pagination
        total_pages = max(
            1, (len(type_configs) + CONFIGS_PER_PAGE - 1) // CONFIGS_PER_PAGE
        )
        start_idx = page * CONFIGS_PER_PAGE
        end_idx = min(start_idx + CONFIGS_PER_PAGE, len(type_configs))
        page_configs = type_configs[start_idx:end_idx]

        # Build message
        type_name, emoji = _get_controller_type_display(current_type)
        lines = [r"⚙️ *Controller Configs*", ""]

        # Show selected summary (always visible)
        if selected_ids:
            lines.append(f"✅ *Selected \\({len(selected_ids)}\\):*")
            for cfg_id in selected_ids[:5]:  # Show max 5
                lines.append(f"  • `{escape_markdown_v2(cfg_id)}`")
            if len(selected_ids) > 5:
                lines.append(f"  _\\.\\.\\.and {len(selected_ids) - 5} more_")
            lines.append("")

        # Current type info
        if type_configs:
            if total_pages > 1:
                lines.append(
                    f"_{len(type_configs)} {escape_markdown_v2(type_name)} configs \\(page {page + 1}/{total_pages}\\)_"
                )
            else:
                lines.append(
                    f"_{len(type_configs)} {escape_markdown_v2(type_name)} config{'s' if len(type_configs) != 1 else ''}_"
                )
        else:
            lines.append(f"_No {escape_markdown_v2(type_name)} configs yet_")

        # Build keyboard
        keyboard = []

        # Row 1: Type selector + Create buttons
        type_row = []
        # Type selector button (shows current type, click to change)
        if len(all_types) > 1:
            type_row.append(
                InlineKeyboardButton(
                    text=f"{emoji} {type_name} ▼", callback_data="bots:cfg_select_type"
                )
            )
        else:
            type_row.append(
                InlineKeyboardButton(
                    text=f"{emoji} {type_name}", callback_data="bots:noop"
                )
            )

        # Create button - dynamically routes to the current type's wizard
        type_row.append(
            InlineKeyboardButton(
                text="➕ New", callback_data=f"bots:new_{current_type}"
            )
        )

        keyboard.append(type_row)

        # Config checkboxes - show just the controller name/ID
        for i, cfg in enumerate(page_configs):
            config_id = cfg.get("id", f"config_{start_idx + i}")
            is_selected = selected.get(config_id, False)
            checkbox = "✅" if is_selected else "⬜"

            # Show just the config ID (truncated if needed)
            display = f"{checkbox} {config_id[:28]}"

            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=display, callback_data=f"bots:cfg_toggle:{config_id}"
                    )
                ]
            )

        # Pagination row
        if total_pages > 1:
            nav = []
            if page > 0:
                nav.append(
                    InlineKeyboardButton(
                        text="‹ Prev", callback_data=f"bots:cfg_page:{page - 1}"
                    )
                )
            nav.append(
                InlineKeyboardButton(
                    text=f"📄 {page + 1}/{total_pages}", callback_data="bots:noop"
                )
            )
            if page < total_pages - 1:
                nav.append(
                    InlineKeyboardButton(
                        text="Next ›", callback_data=f"bots:cfg_page:{page + 1}"
                    )
                )
            keyboard.append(nav)

        # Action buttons (only if something selected)
        if selected_ids:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=f"🚀 Deploy ({len(selected_ids)})",
                        callback_data="bots:cfg_deploy",
                    ),
                    InlineKeyboardButton(
                        text=f"✏️ Edit ({len(selected_ids)})",
                        callback_data="bots:cfg_edit_loop",
                    ),
                ]
            )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=f"🗑 Delete ({len(selected_ids)})",
                        callback_data="bots:cfg_delete_confirm",
                    ),
                    InlineKeyboardButton(
                        text="⬜ Clear", callback_data="bots:cfg_clear_selection"
                    ),
                ]
            )

        keyboard.append(
            [
                InlineKeyboardButton(
                    text="📤 Upload", callback_data="bots:upload_config"
                ),
                InlineKeyboardButton(text="« Back", callback_data="bots:main_menu"),
            ]
        )

        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        text_content = "\n".join(lines)

        # Handle command vs callback
        if query and query.message:
            # Called from callback - edit the message
            if getattr(query.message, "photo", None):
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await query.message.answer(
                    text_content, parse_mode="MarkdownV2", reply_markup=reply_markup
                )
            else:
                try:
                    await query.message.edit_text(
                        text_content, parse_mode="MarkdownV2", reply_markup=reply_markup
                    )
                except BadRequest as e:
                    if not is_not_modified(e):
                        raise
        else:
            # Called from command - send new message
            msg = update.message
            if msg:
                await msg.answer(
                    text_content, parse_mode="MarkdownV2", reply_markup=reply_markup
                )

    except Exception as e:
        logger.error(f"Error loading controller configs: {e}", exc_info=True)
        keyboard = []
        for ctrl_type in get_supported_controller_types():
            type_name, emoji = _get_controller_type_display(ctrl_type)
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=f"➕ {type_name}", callback_data=f"bots:new_{ctrl_type}"
                    )
                ]
            )
        keyboard.append(
            [InlineKeyboardButton(text="« Back", callback_data="bots:main_menu")]
        )
        error_msg = format_error_message(f"Failed to load configs: {str(e)}")
        try:
            if query and query.message:
                if getattr(query.message, "photo", None):
                    try:
                        await query.message.delete()
                    except Exception:
                        pass
                    await query.message.answer(
                        error_msg,
                        parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                    )
                else:
                    await query.message.edit_text(
                        error_msg,
                        parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                    )
            elif update.message:
                await update.message.answer(
                    error_msg,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                )
        except Exception:
            pass


async def show_type_selector(update: Update, context: Context) -> None:
    """Show type selector popup to switch between controller types"""
    query = update.callback_query
    configs = context.user_data.get("controller_configs_list", [])

    type_counts = _get_available_controller_types(configs)
    current_type = context.user_data.get("configs_controller_type", "grid_strike")

    lines = [r"*Select Controller Type*", ""]

    # Show all registered types from registry (not just existing configs)
    keyboard = []
    for ctrl_type, info in get_controller_info().items():
        type_name, emoji = _get_controller_type_display(ctrl_type)
        count = type_counts.get(ctrl_type, 0)
        is_current = "• " if ctrl_type == current_type else ""
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"{is_current}{emoji} {type_name} ({count})",
                    callback_data=f"bots:cfg_type:{ctrl_type}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="↩️ Cancel", callback_data="bots:controller_configs"
            ),
        ]
    )

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def show_configs_by_type(
    update: Update,
    context: Context,
    controller_type: str,
    page: int = 0,
) -> None:
    """Switch to a specific controller type and show configs"""
    context.user_data["configs_controller_type"] = controller_type
    context.user_data["configs_page"] = page
    await show_controller_configs_menu(update, context, page)


async def handle_cfg_toggle(update: Update, context: Context, config_id: str) -> None:
    """Toggle config selection by config ID"""
    selected = context.user_data.get("selected_configs", {})

    if selected.get(config_id):
        selected.pop(config_id, None)
    else:
        selected[config_id] = True

    context.user_data["selected_configs"] = selected

    page = context.user_data.get("configs_page", 0)
    await show_controller_configs_menu(update, context, page)


async def handle_cfg_page(update: Update, context: Context, page: int) -> None:
    """Handle pagination for configs"""
    await show_controller_configs_menu(update, context, page)


async def handle_cfg_clear_selection(update: Update, context: Context) -> None:
    """Clear all selected configs"""
    context.user_data["selected_configs"] = {}
    page = context.user_data.get("configs_page", 0)
    await show_controller_configs_menu(update, context, page)


async def handle_cfg_delete_confirm(update: Update, context: Context) -> None:
    """Show delete confirmation dialog"""
    query = update.callback_query
    selected = context.user_data.get("selected_configs", {})
    selected_ids = [cfg_id for cfg_id, is_sel in selected.items() if is_sel]

    if not selected_ids:
        await query.answer("No configs selected", show_alert=True)
        return

    # Build confirmation message
    lines = [r"*Delete Configs\?*", ""]
    lines.append(
        f"You are about to delete {len(selected_ids)} config{'s' if len(selected_ids) != 1 else ''}:"
    )
    lines.append("")

    for cfg_id in selected_ids:
        lines.append(f"• `{escape_markdown_v2(cfg_id)}`")

    lines.append("")
    lines.append(r"⚠️ _This action cannot be undone\._")

    keyboard = [
        [
            InlineKeyboardButton(
                text="✅ Yes, Delete", callback_data="bots:cfg_delete_execute"
            ),
            InlineKeyboardButton(
                text="↩️ Cancel", callback_data="bots:controller_configs"
            ),
        ]
    ]

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    text_content = "\n".join(lines)

    await query.message.edit_text(
        text_content, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


# How many config writes a bulk delete or save has in flight at once. Bounded
# so a user selecting thirty configs does not open thirty connections to the
# API while still not paying for them one after another.
_BULK_CONCURRENCY = 4


async def _run_batched(config_ids, operation, *, verb: str):
    """Apply ``operation`` to each id, at most ``_BULK_CONCURRENCY`` at a time.

    Returns ``(succeeded, failed)`` in the input order, ``failed`` holding
    ``(config_id, error)`` pairs, so the result card reads the same as the old
    serial loop produced.
    """
    semaphore = asyncio.Semaphore(_BULK_CONCURRENCY)

    async def one(config_id):
        async with semaphore:
            try:
                await operation(config_id)
                return config_id, None
            except Exception as e:
                logger.error(f"Failed to {verb} config {config_id}: {e}")
                return config_id, str(e)

    outcomes = await asyncio.gather(*(one(config_id) for config_id in config_ids))
    succeeded = [config_id for config_id, error in outcomes if error is None]
    failed = [(config_id, error) for config_id, error in outcomes if error is not None]
    return succeeded, failed


async def handle_cfg_delete_execute(update: Update, context: Context) -> None:
    """Execute deletion of selected configs"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    selected = context.user_data.get("selected_configs", {})
    selected_ids = [cfg_id for cfg_id, is_sel in selected.items() if is_sel]

    if not selected_ids:
        await query.answer("No configs selected", show_alert=True)
        return

    # Show progress
    await query.message.edit_text(
        f"🗑️ Deleting {len(selected_ids)} config{'s' if len(selected_ids) != 1 else ''}\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    # Delete each config
    client, _ = await get_bots_client(chat_id, context.user_data)
    deleted, failed = await _run_batched(
        selected_ids,
        lambda config_id: client.controllers.delete_controller_config(config_id),
        verb="delete",
    )

    # Clear selection
    context.user_data["selected_configs"] = {}

    # Build result message
    lines = []
    if deleted:
        lines.append(
            f"✅ *Deleted {len(deleted)} config{'s' if len(deleted) != 1 else ''}*"
        )
        for cfg_id in deleted:
            lines.append(f"  • `{escape_markdown_v2(cfg_id)}`")

    if failed:
        lines.append("")
        lines.append(f"❌ *Failed to delete {len(failed)}:*")
        for cfg_id, error in failed:
            lines.append(f"  • `{escape_markdown_v2(cfg_id)}`")
            lines.append(f"    _{escape_markdown_v2(error[:40])}_")

    keyboard = [
        [InlineKeyboardButton(text="« Back", callback_data="bots:controller_configs")]
    ]

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def handle_cfg_deploy(update: Update, context: Context) -> None:
    """Deploy selected configs - bridges to existing deploy flow"""
    selected = context.user_data.get("selected_configs", {})
    selected_ids = [cfg_id for cfg_id, is_sel in selected.items() if is_sel]
    all_configs = context.user_data.get("controller_configs_list", [])

    if not selected_ids:
        query = update.callback_query
        await query.answer("No configs selected", show_alert=True)
        return

    # Map config IDs to all_configs indices for existing deploy flow
    deploy_indices = set()
    for cfg_id in selected_ids:
        for all_idx, all_cfg in enumerate(all_configs):
            if all_cfg.get("id") == cfg_id:
                deploy_indices.add(all_idx)
                break

    # Set up for existing deploy flow
    context.user_data["selected_controllers"] = deploy_indices

    # Don't clear selection - keep it for when user comes back

    # Use existing deploy configure flow
    await show_deploy_configure(update, context)
