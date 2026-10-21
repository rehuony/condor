"""Persistent per-routine, per-owner post-execution hooks.

After a routine finishes, its report can be delivered to extra destinations:
    - Telegram — the raw interactive .html report as a document.

Config is a persistent preset stored in ``data/routine_hooks.json``, keyed by
the routine name as known to each engine (global base name, or ``slug/name``
for agent routines) and then by the id of the user who configured it. It
applies to every execution of that routine *by that owner* (manual, scheduled,
or after restart).

``trigger`` controls when to fire: "success" (default), "always", or "failure".

Each user reads and changes their own hooks. A run fires only its owner's
hooks, and recipients are checked both when saving and when dispatching.
Users may target their own Telegram chat; admins may also target shared
groups and other chats.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from condor import paths
from condor.fsutil import atomic_write_json
from condor.telegram.transport import as_input_file

logger = logging.getLogger(__name__)

# Valid trigger conditions.
_TRIGGERS = ("success", "always", "failure")


class ForbiddenRecipient(Exception):
    """Raised when a hook targets a chat its owner is not allowed to reach."""


# ── Ownership ──


def _is_admin(user_id: int | None) -> bool:
    if not user_id:
        return False
    try:
        from config_manager import get_config_manager

        return bool(get_config_manager().is_admin(int(user_id)))
    except Exception:  # noqa: BLE001
        # Fail closed: an unresolvable role is not an admin.
        logger.warning("Could not resolve role for user %s", user_id, exc_info=True)
        return False


def _allowed_recipient(chat_id: str, user_id: int | None) -> bool:
    """Whether ``user_id`` may have this routine's reports delivered to ``chat_id``.

    A plain user only reaches their own Telegram chat — the one Condor already
    talks to them in — so a hook can never carry a report somewhere its owner
    does not control. Admins may target any chat: they route reports into
    shared groups and channels.
    """
    if not user_id:
        return False
    return chat_id == str(user_id) or _is_admin(user_id)


# ── Persistence ──


def _read_all() -> dict[str, dict[str, dict]]:
    path = paths.routine_hooks_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return {}
        return {
            name: {
                str(owner): cfg
                for owner, cfg in owners.items()
                if isinstance(cfg, dict)
            }
            for name, owners in data.items()
            if isinstance(owners, dict)
        }
    except Exception:  # noqa: BLE001
        logger.warning("Failed to read %s; treating as empty", path)
        return {}


def _write_all(data: dict[str, dict[str, dict]]) -> None:
    atomic_write_json(paths.routine_hooks_path(), data, indent=2)


def _default_config() -> dict:
    return {
        "telegram": {"enabled": False, "chat_ids": []},
        "trigger": "success",
    }


def load_hooks(routine_name: str, user_id: int | None) -> dict | None:
    """Return the owner's hook config for a routine, or None if none is set."""
    if not user_id:
        return None
    owners = _read_all().get(routine_name) or {}
    return owners.get(str(user_id))


def save_hooks(routine_name: str, cfg: dict, user_id: int | None) -> dict:
    """Validate and persist ``user_id``'s hook config. Returns the stored config.

    Raises :class:`ForbiddenRecipient` if a chat id is not one the owner may
    deliver to.
    """
    if not user_id:
        raise ForbiddenRecipient("Hooks need an owner")

    clean = _default_config()

    tg = cfg.get("telegram") or {}
    chat_ids = [str(c).strip() for c in (tg.get("chat_ids") or []) if str(c).strip()]
    # Light validation: chat ids are integers (may be negative for groups).
    chat_ids = [c for c in chat_ids if c.lstrip("-").isdigit()]
    forbidden = [c for c in chat_ids if not _allowed_recipient(c, user_id)]
    if forbidden:
        raise ForbiddenRecipient(
            "You can only send routine reports to your own chat "
            f"({user_id}); not allowed: {', '.join(forbidden)}"
        )
    clean["telegram"] = {"enabled": bool(tg.get("enabled")), "chat_ids": chat_ids}

    trigger = cfg.get("trigger")
    clean["trigger"] = trigger if trigger in _TRIGGERS else "success"

    data = _read_all()
    owners = data.setdefault(routine_name, {})
    # If nothing is enabled/configured, drop the entry to keep the file tidy.
    if not clean["telegram"]["enabled"] and not chat_ids:
        owners.pop(str(user_id), None)
    else:
        owners[str(user_id)] = clean
    if not owners:
        data.pop(routine_name, None)
    _write_all(data)
    return clean


# ── Dispatch ──


def _should_fire(trigger: str, failed: bool) -> bool:
    if trigger == "always":
        return True
    if trigger == "failure":
        return failed
    # default: success
    return not failed


def _resolve_report_html(report_id: str | None, result) -> tuple[str, str]:
    """Return (html_content, filename) for the report.

    Uses the raw interactive report HTML, falling back to a minimal HTML
    document built from the result text when the routine produced no report.
    """
    if report_id:
        try:
            from condor.reports import get_report_raw_html, hydrate

            found = get_report_raw_html(report_id)
            if found:
                # Telegram is off-origin: nothing there resolves the shared
                # plotly bundle a stored report references, so inline it back
                # (PERF-267). A report with no reference passes through.
                return hydrate(found[0]), found[1]
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not load report %s: %s", report_id, e)

    text = getattr(result, "text", "") or "Completed"
    import html as _html

    body = _html.escape(text)
    minimal = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>body{font-family:-apple-system,sans-serif;background:#0d1117;"
        "color:#e6edf3;padding:24px;}pre{white-space:pre-wrap;}</style></head>"
        f"<body><pre>{body}</pre></body></html>"
    )
    return minimal, "report.html"


async def dispatch(
    routine_name: str,
    result,
    report_id: str | None,
    *,
    failed: bool,
    bot: Any | None,
    owner_id: int | None,
) -> None:
    """Send the Telegram notification ``owner_id`` configured for a routine.

    ``owner_id`` is who started this run — ``user_id`` in the RoutineStore
    instance meta, the chat id on the Telegram path. Only that user's hooks
    fire, so a run never delivers its report to someone else's destinations
    (SEC-152). A run with no owner fires nothing.

    Never raises — failures are logged so a broken notification can't break the
    routine run itself.
    """
    cfg = load_hooks(routine_name, owner_id)
    if not cfg:
        return

    trigger = cfg.get("trigger", "success")
    if not _should_fire(trigger, failed):
        return

    tg_cfg = cfg.get("telegram") or {}
    # Re-check recipients at dispatch: permissions can change after saving,
    # and a hand-edited file must not bypass the current recipient policy.
    chat_ids = [
        c
        for c in (tg_cfg.get("chat_ids") or [])
        if _allowed_recipient(str(c), owner_id)
    ]
    if not (tg_cfg.get("enabled") and chat_ids and bot is not None):
        return

    # Send the raw interactive report HTML (live Plotly charts), or a minimal
    # fallback when the routine produced no report.
    html, _filename = _resolve_report_html(report_id, result)

    # Caption = the report's title (falls back to the routine name).
    report_title = None
    if report_id:
        try:
            from condor.reports import get_report

            entry = get_report(report_id)
            if entry:
                report_title = entry.get("title")
        except Exception:  # noqa: BLE001
            pass
    # Use a clean, human-friendly filename (the report title) so the document
    # arrives "bare" — like a manually dragged file — instead of the
    # auto-generated on-disk name. No caption: a caption makes Telegram wrap
    # the file in a message bubble; without it the file shows plain.
    display_name = report_title or routine_name
    slug = re.sub(r"[^\w.-]+", "_", display_name).strip("_") or "report"
    if not slug.lower().endswith(".html"):
        slug += ".html"

    doc_bytes = html.encode("utf-8")
    for chat_id in chat_ids:
        try:
            import io

            buf = io.BytesIO(doc_bytes)
            buf.name = slug
            await bot.send_document(
                chat_id=int(chat_id),
                document=as_input_file(buf),
            )
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Telegram hook failed for %s -> %s: %s", routine_name, chat_id, e
            )
