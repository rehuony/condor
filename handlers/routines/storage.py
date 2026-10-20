"""Per-user routine drafts and instances in user_data, synced to the routine store."""

import asyncio
import hashlib
import logging
import time

from condor.preferences import SERVER_PIN_KEY, get_active_server, set_active_server
from condor.routine_store import get_routine_store
from condor.telegram.types import (
    Context,
    Update,
)
from routines.base import (
    get_routine,
)

logger = logging.getLogger(__name__)


SCHEDULE_PRESETS = [
    ("30s", 30),
    ("1m", 60),
    ("5m", 300),
    ("15m", 900),
    ("30m", 1800),
    ("1h", 3600),
]


DAILY_PRESETS = ["06:00", "09:00", "12:00", "18:00", "21:00"]


# Global storage for continuous routine tasks (not persisted)
_continuous_tasks: dict[str, asyncio.Task] = {}  # instance_id -> Task


def _get_drafts(context: Context) -> dict:
    """Get draft configs dict."""
    if "routine_drafts" not in context.user_data:
        context.user_data["routine_drafts"] = {}
    return context.user_data["routine_drafts"]


def _get_instances(context: Context) -> dict:
    """Get all instances dict."""
    if "routine_instances" not in context.user_data:
        context.user_data["routine_instances"] = {}
    return context.user_data["routine_instances"]


def _owner_data(application, owner_id: int | None, chat_id: int) -> dict:
    """The starter's ``user_data`` bucket, as seen from a job or a task.

    The bot keys ``user_data`` by ``update.effective_user.id``, which is what
    ``_get_instances`` writes through — so a job must look itself up by the
    owner stamped at creation, never by ``chat_id``. In a private chat the two
    ids coincide; in a group the chat id is negative and its bucket does not
    exist at all.

    ``owner_id`` falls back to ``chat_id`` so private chats and jobs persisted
    before the owner was carried in ``job_data`` resolve exactly as before —
    the same graceful degradation ``_sync_instance_to_store`` applies.
    """
    return application.user_data.get(owner_id if owner_id is not None else chat_id, {})


def _bind_launch_server(user_data: dict, active_server: str | None) -> None:
    """Record the server a run was launched against, for ``get_client()``.

    Both Telegram runners capture ``get_effective_server()`` at creation time —
    ``user_data`` is keyed by user id, so a job or a task ticking later in a
    group chat cannot look the room's server up for itself. The capture only
    reaches the client if it is written where the preference API reads: both
    call sites hand-rolled a walk down a bare ``preferences`` key that nothing
    in the repo reads, writes or migrates, so the injection was inert and every
    such run silently resolved whatever ambient default it found — the same
    key mismatch the web context carried until ``test_routine_server_scope``
    pinned it down, left behind in the two Telegram runners (CORR-221).

    Pinned, not merely preferred: the captured name *is* the server the user
    was looking at when they pressed Run, and a scheduled or continuous run
    ticks long after that. Without the mark ``get_effective_server`` treats
    ``active_server`` as a cache of the chat default and lets a ``chat_defaults``
    entry recorded later overwrite it mid-run — moving a running routine onto
    another server, which is exactly what capturing the name was meant to stop.

    An owner who already has a server of their own keeps it: the guard reads
    through the same API it writes through, so it is now a real check rather
    than one that always fell through to the write.
    """
    if not active_server or get_active_server(user_data):
        return
    set_active_server(user_data, active_server)
    user_data[SERVER_PIN_KEY] = True


def _get_draft(context: Context, routine_name: str) -> dict:
    """Get draft config for a routine, initializing from defaults if needed."""
    drafts = _get_drafts(context)
    if routine_name not in drafts:
        routine = get_routine(routine_name)
        if routine:
            drafts[routine_name] = routine.get_default_config().model_dump()
        else:
            drafts[routine_name] = {}
    return drafts[routine_name]


def _set_draft(context: Context, routine_name: str, config: dict) -> None:
    """Update draft config for a routine."""
    drafts = _get_drafts(context)
    drafts[routine_name] = config


def _get_routine_instances(
    context: Context, routine_name: str
) -> list[tuple[str, dict]]:
    """Get all instances for a specific routine."""
    instances = _get_instances(context)
    return [
        (iid, inst)
        for iid, inst in instances.items()
        if inst.get("routine_name") == routine_name
    ]


def _starter_id(update: Update) -> int | None:
    """The user who pressed the button — the owner of whatever they start.

    ``chat_id`` only coincides with it in private chats; in a group the routine
    belongs to the member who launched it, not to the group.
    """
    return update.effective_user.id if update.effective_user else None


def _generate_instance_id() -> str:
    """Generate a short unique instance ID."""
    return hashlib.md5(f"{time.time()}{id(object())}".encode()).hexdigest()[:6]


def _sync_instance_to_store(instance_id: str, inst_meta: dict) -> None:
    """Sync a Telegram instance's state to the shared RoutineStore.

    The owner (``user_id``, stamped at creation) is never downgraded: a later
    tick whose meta lost it must not turn an owned instance into an unowned
    one, which the dashboard's ownership gate (SEC-150) would hide from its
    own starter.
    """
    try:
        store = get_routine_store()
        existing = store._instances.get(instance_id)
        if existing:
            update = dict(inst_meta)
            if not update.get("user_id") and existing.get("user_id"):
                update.pop("user_id", None)
            existing.update(update)
        else:
            store.add_instance(instance_id, inst_meta.copy())
    except Exception:
        pass
