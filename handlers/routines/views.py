"""Routine menu, task list, detail and schedule screens."""

import asyncio
import logging
import time

from condor.telegram.errors import is_not_modified
from condor.telegram.transport import as_input_file
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from routines.base import (
    discover_routines,
    get_routine,
)
from utils.telegram_formatters import escape_markdown_v2, escape_markdown_v2_code

from .formatting import (
    _config_preview,
    _display_name,
    _format_ago,
    _format_duration,
    _format_schedule,
)
from .storage import (
    DAILY_PRESETS,
    SCHEDULE_PRESETS,
    _get_draft,
    _get_instances,
    _get_routine_instances,
    _owner_data,
)

logger = logging.getLogger(__name__)


async def _edit_or_send(
    update: Update, text: str, reply_markup: InlineKeyboardMarkup
) -> None:
    """Edit message if callback, otherwise send new."""
    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(
                text, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
        except Exception as e:
            if not is_not_modified(e):
                logger.warning(f"Edit failed: {e}")
    else:
        msg = update.message or update.callback_query.message
        await msg.answer(text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def _show_menu(update: Update, context: Context) -> None:
    """Show main routines menu."""
    chat_id = update.effective_chat.id
    # Plain discovery: it is mtime-keyed, so new/edited/deleted routine files are
    # still picked up on every render (PERF-620). ``force_reload=True`` is kept for
    # the explicit Reload button, whose job is to retry cached load *failures*.
    routines = discover_routines()
    all_instances = _get_instances(context)

    # Count running instances per routine
    running_counts = {}
    for inst in all_instances.values():
        rname = inst.get("routine_name")
        if inst.get("status") == "running":
            running_counts[rname] = running_counts.get(rname, 0) + 1

    total_running = sum(running_counts.values())

    if not routines:
        text = (
            "⚡ *Routines*\n\n"
            "No routines found\\.\n\n"
            "Add Python files to `routines/` folder\\."
        )
        keyboard = [
            [InlineKeyboardButton(text="🔄 Refresh", callback_data="routines:reload")]
        ]
    else:
        keyboard = []

        if total_running > 0:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=f"📋 Running ({total_running})",
                        callback_data="routines:tasks",
                    )
                ]
            )

        for name in sorted(routines.keys()):
            routine = routines[name]
            count = running_counts.get(name, 0)

            if count > 0:
                label = f"🟢 {_display_name(name)} ({count})"
            else:
                icon = "♾️" if routine.is_continuous else "⚡"
                label = f"{icon} {_display_name(name)}"

            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=label, callback_data=f"routines:select:{name}"
                    )
                ]
            )

        keyboard.append(
            [InlineKeyboardButton(text="🔄 Refresh", callback_data="routines:reload")]
        )

        status = f"🟢 {total_running} running" if total_running else "All idle"
        text = (
            "⚡ *Routines*\n"
            f"_{escape_markdown_v2(status)}_\n\n"
            "_Select a routine to configure and run\\._"
        )

    await _edit_or_send(update, text, InlineKeyboardMarkup(inline_keyboard=keyboard))


async def _show_tasks(update: Update, context: Context) -> None:
    """Show all running tasks."""
    chat_id = update.effective_chat.id
    instances = _get_instances(context)

    running = [
        (iid, inst)
        for iid, inst in instances.items()
        if inst.get("status") == "running"
    ]

    if not running:
        text = "📋 *Running Tasks*\n\n" "No tasks running\\."
        keyboard = [
            [InlineKeyboardButton(text="« Back", callback_data="routines:menu")]
        ]
    else:
        lines = ["📋 *Running Tasks*", ""]
        keyboard = []

        for iid, inst in running:
            name = inst["routine_name"]
            schedule = inst.get("schedule", {})
            created = inst.get("created_at", time.time())
            config = inst.get("config", {})
            run_count = inst.get("run_count", 0)

            lines.append(f"🟢 *{escape_markdown_v2(_display_name(name))}* `{iid}`")
            lines.append(
                f"   {escape_markdown_v2(_format_schedule(schedule))} \\| {escape_markdown_v2(_format_ago(created))}"
            )
            lines.append(
                f"   Runs: {run_count} \\| `{escape_markdown_v2(_config_preview(config))}`"
            )

            if inst.get("last_result"):
                result_preview = inst["last_result"][:40].replace("\n", " ")
                lines.append(f"   └ {escape_markdown_v2(result_preview)}\\.\\.\\.")
            lines.append("")

            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=f"⏹ {_display_name(name)[:12]}[{iid}]",
                        callback_data=f"routines:stop:{iid}",
                    )
                ]
            )

        keyboard.append(
            [InlineKeyboardButton(text="⏹ Stop All", callback_data="routines:stopall")]
        )
        keyboard.append(
            [InlineKeyboardButton(text="« Back", callback_data="routines:menu")]
        )
        text = "\n".join(lines)

    await _edit_or_send(update, text, InlineKeyboardMarkup(inline_keyboard=keyboard))


async def _show_detail(update: Update, context: Context, routine_name: str) -> None:
    """Show routine detail with draft config and running instances."""
    chat_id = update.effective_chat.id
    routine = get_routine(routine_name)

    if not routine:
        if update.callback_query:
            await update.callback_query.answer("Routine not found")
        return

    # Get draft config
    draft = _get_draft(context, routine_name)
    fields = routine.get_fields()

    # Get running instances for this routine
    instances = _get_routine_instances(context, routine_name)
    running = [
        (iid, inst) for iid, inst in instances if inst.get("status") == "running"
    ]

    # Build config display
    config_lines = [f"{k}={draft.get(k, v['default'])}" for k, v in fields.items()]

    # Status line
    if running:
        status = f"🟢 {len(running)} running"
    else:
        status = "⚪ Ready"

    # Type indicator
    type_str = "♾️ Continuous" if routine.is_continuous else "⚡ One\\-shot"

    # Build instances section
    inst_section = ""
    if running:
        inst_lines = ["\n*Running Instances*"]
        for iid, inst in running[:5]:
            sched = _format_schedule(inst.get("schedule", {}))
            ago = _format_ago(inst.get("created_at", time.time()))
            cfg_prev = _config_preview(inst.get("config", {}), 1)
            runs = inst.get("run_count", 0)
            inst_lines.append(f"• `{iid}` {escape_markdown_v2(cfg_prev)}")
            inst_lines.append(
                f"   {escape_markdown_v2(sched)} · {runs} runs · {escape_markdown_v2(ago)}"
            )
        if len(running) > 5:
            inst_lines.append(f"_\\+{len(running) - 5} more_")
        inst_section = "\n".join(inst_lines)

    text = (
        f"⚡ *{escape_markdown_v2(_display_name(routine_name))}*\n"
        f"_{escape_markdown_v2(routine.description)}_\n\n"
        f"• Type: *{type_str}*\n"
        f"• Status: *{escape_markdown_v2(status)}*\n\n"
        f"*Config*\n"
        f"```\n{escape_markdown_v2_code(chr(10).join(config_lines))}\n```\n"
        f"_✏️ Send key\\=value to edit\\._"
        f"{inst_section}"
    )

    # Build keyboard based on routine type
    if routine.is_continuous:
        # Continuous routine - just start/stop
        keyboard = [
            [
                InlineKeyboardButton(
                    text="▶️ Start", callback_data=f"routines:start:{routine_name}"
                ),
            ],
        ]
    else:
        # One-shot routine - can be scheduled
        keyboard = [
            [
                InlineKeyboardButton(
                    text="▶️ Run", callback_data=f"routines:run:{routine_name}"
                ),
                InlineKeyboardButton(
                    text="🌙 Background", callback_data=f"routines:bg:{routine_name}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⏱️ Schedule", callback_data=f"routines:sched:{routine_name}"
                ),
            ],
        ]

    if running:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"⏹ Stop All ({len(running)})",
                    callback_data=f"routines:stopall:{routine_name}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="❓ Help", callback_data=f"routines:help:{routine_name}"
            ),
            InlineKeyboardButton(text="« Back", callback_data="routines:menu"),
        ]
    )

    # Store editing state
    context.user_data["routines_state"] = "editing"
    context.user_data["routines_editing"] = {
        "routine": routine_name,
        "fields": fields,
    }

    msg = update.callback_query.message if update.callback_query else None
    if msg:
        context.user_data["routines_msg_id"] = msg.message_id
        context.user_data["routines_chat_id"] = msg.chat.id

    await _edit_or_send(update, text, InlineKeyboardMarkup(inline_keyboard=keyboard))


async def _show_schedule_menu(
    update: Update, context: Context, routine_name: str
) -> None:
    """Show schedule options menu."""
    routine = get_routine(routine_name)
    if not routine:
        return

    text = (
        f"⏱️ *Schedule: {escape_markdown_v2(_display_name(routine_name))}*\n\n"
        f"Choose how often to run this routine\\.\n"
        f"Config will be frozen at schedule time\\.\n"
        f"Results will be sent as messages\\."
    )

    # Interval buttons - 3 per row
    row1 = [
        InlineKeyboardButton(
            text=label, callback_data=f"routines:interval:{routine_name}:{secs}"
        )
        for label, secs in SCHEDULE_PRESETS[:3]
    ]
    row2 = [
        InlineKeyboardButton(
            text=label, callback_data=f"routines:interval:{routine_name}:{secs}"
        )
        for label, secs in SCHEDULE_PRESETS[3:6]
    ]

    keyboard = [
        row1,
        row2,
        [
            InlineKeyboardButton(
                text="📅 Daily...", callback_data=f"routines:daily:{routine_name}"
            )
        ],
        [
            InlineKeyboardButton(
                text="✕ Cancel", callback_data=f"routines:select:{routine_name}"
            )
        ],
    ]

    await _edit_or_send(update, text, InlineKeyboardMarkup(inline_keyboard=keyboard))


async def _show_daily_menu(update: Update, context: Context, routine_name: str) -> None:
    """Show daily schedule time options."""
    text = (
        f"📅 *Daily Schedule: {escape_markdown_v2(_display_name(routine_name))}*\n\n"
        f"Choose a time \\(server timezone\\)\\.\n"
        f"Or send custom time as `HH:MM`\\."
    )

    # Time preset buttons
    row1 = [
        InlineKeyboardButton(
            text=t, callback_data=f"routines:dailyat:{routine_name}:{t}"
        )
        for t in DAILY_PRESETS[:3]
    ]
    row2 = [
        InlineKeyboardButton(
            text=t, callback_data=f"routines:dailyat:{routine_name}:{t}"
        )
        for t in DAILY_PRESETS[3:]
    ]

    keyboard = [
        row1,
        row2,
        [
            InlineKeyboardButton(
                text="« Back", callback_data=f"routines:sched:{routine_name}"
            )
        ],
    ]

    # Store state for custom time input
    context.user_data["routines_state"] = "daily_time"
    context.user_data["routines_editing"] = {"routine": routine_name}

    await _edit_or_send(update, text, InlineKeyboardMarkup(inline_keyboard=keyboard))


async def _share_report(update: Update, context: Context, report_id: str) -> None:
    """Send a report HTML file as a Telegram document."""
    from condor.reports import get_report, get_report_raw_html, hydrate

    chat_id = update.effective_chat.id
    entry = get_report(report_id)
    if not entry:
        await update.callback_query.answer("Report not found", show_alert=True)
        return

    # Read through the guarded helper: the index entry is untrusted, so the
    # resolved path must stay inside the reports directory and be an .html
    # file. A poisoned entry yields None here, not an arbitrary file.
    found = get_report_raw_html(report_id)
    if not found:
        await update.callback_query.answer("Report file missing", show_alert=True)
        return

    try:
        await context.bot.send_document(
            chat_id=chat_id,
            # Hydrated: the file leaves the origin, so the shared plotly
            # bundle it references gets inlined back in (PERF-267).
            document=as_input_file(
                hydrate(found[0]).encode("utf-8"), filename=f"{entry['title']}.html"
            ),
            caption=f"📊 {entry['title']}",
        )
    except Exception as e:
        logger.error(f"Failed to share report {report_id}: {e}")
        await update.callback_query.answer(f"Error: {e}", show_alert=True)


async def _show_help(update: Update, context: Context, routine_name: str) -> None:
    """Show field descriptions."""
    routine = get_routine(routine_name)
    if not routine:
        return

    lines = [
        f"❓ *{escape_markdown_v2(_display_name(routine_name))}*",
        "",
    ]

    for name, info in routine.get_fields().items():
        lines.append(
            f"• `{escape_markdown_v2(name)}` _{escape_markdown_v2(info['type'])}_"
        )
        lines.append(f"  {escape_markdown_v2(info['description'])}")
        lines.append(f"  Default: `{escape_markdown_v2(str(info['default']))}`\n")

    keyboard = [
        [
            InlineKeyboardButton(
                text="« Back", callback_data=f"routines:select:{routine_name}"
            )
        ]
    ]
    await _edit_or_send(
        update, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


async def _refresh_detail_msg(
    context: Context,
    chat_id: int,
    msg_id: int,
    routine_name: str,
    result: str | None = None,
    duration: float | None = None,
    report_id: str | None = None,
    owner_id: int | None = None,
) -> None:
    """Refresh the routine detail message after execution.

    Drafts and instances belong to ``owner_id``; ``chat_id`` only says where the
    message lives.
    """
    routine = get_routine(routine_name)
    if not routine:
        return

    user_data = _owner_data(context.application, owner_id, chat_id)
    drafts = user_data.get("routine_drafts", {})
    draft = drafts.get(routine_name, {})

    if not draft:
        draft = routine.get_default_config().model_dump()

    fields = routine.get_fields()
    config_lines = [f"{k}={draft.get(k, v['default'])}" for k, v in fields.items()]

    # Get instances
    instances = user_data.get("routine_instances", {})
    running = [
        (iid, inst)
        for iid, inst in instances.items()
        if inst.get("routine_name") == routine_name and inst.get("status") == "running"
    ]

    status = f"🟢 {len(running)} running" if running else "⚪ Ready"
    type_str = "♾️ Continuous" if routine.is_continuous else "⚡ One\\-shot"

    # Result section
    result_section = ""
    if result is not None:
        icon = "❌" if result.startswith("Error") else "✅"
        dur_str = _format_duration(duration) if duration else ""
        result_section = (
            f"\n\n*{icon} Result*"
            + (f" _{escape_markdown_v2(dur_str)}_" if dur_str else "")
            + f"\n```\n{escape_markdown_v2_code(result[:250])}\n```"
        )

    text = (
        f"⚡ *{escape_markdown_v2(_display_name(routine_name))}*\n"
        f"_{escape_markdown_v2(routine.description)}_\n\n"
        f"• Type: *{type_str}*\n"
        f"• Status: *{escape_markdown_v2(status)}*\n\n"
        f"*Config*\n"
        f"```\n{escape_markdown_v2_code(chr(10).join(config_lines))}\n```\n"
        f"_✏️ Send key\\=value to edit\\._"
        f"{result_section}"
    )

    # Build keyboard based on routine type
    if routine.is_continuous:
        keyboard = [
            [
                InlineKeyboardButton(
                    text="▶️ Start", callback_data=f"routines:start:{routine_name}"
                ),
            ],
        ]
    else:
        keyboard = [
            [
                InlineKeyboardButton(
                    text="▶️ Run", callback_data=f"routines:run:{routine_name}"
                ),
                InlineKeyboardButton(
                    text="🌙 Background", callback_data=f"routines:bg:{routine_name}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⏱️ Schedule", callback_data=f"routines:sched:{routine_name}"
                ),
            ],
        ]

    if running:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"⏹ Stop All ({len(running)})",
                    callback_data=f"routines:stopall:{routine_name}",
                )
            ]
        )

    if report_id:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="📤 Share Report", callback_data=f"routines:share:{report_id}"
                ),
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="❓ Help", callback_data=f"routines:help:{routine_name}"
            ),
            InlineKeyboardButton(text="« Back", callback_data="routines:menu"),
        ]
    )

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
    except Exception as e:
        if not is_not_modified(e):
            logger.debug(f"Could not refresh: {e}")


async def _delete_after(message, seconds: float) -> None:
    """Delete message after delay."""
    await asyncio.sleep(seconds)
    try:
        await message.delete()
    except Exception:
        pass
