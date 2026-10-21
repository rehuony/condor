"""Conversation-wide ordering and change notifications for every transport.

Sessions keep their own model clients. A conversation owns the execution queue,
so switching between Telegram and the dashboard cannot run two turns against
different snapshots of the same history at once.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from itertools import count

log = logging.getLogger(__name__)
ChangeSink = Callable[[dict], Awaitable[None]]
_sinks: list[ChangeSink] = []
_deliveries: set[asyncio.Task] = set()
_sequence = count(1)


@dataclass
class ConversationTurns:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Serializes the durable acceptance writes, which run in worker threads,
    # so the transcript records inputs in the order they arrived.
    accept_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: dict[str, str] = field(default_factory=dict)
    active_key: str = ""
    active_request: str = ""
    cancelled: set[str] = field(default_factory=set)
    cancel_events: dict[str, asyncio.Event] = field(default_factory=dict)
    completed: set[str] = field(default_factory=set)


_turns: dict[tuple[int | None, str], ConversationTurns] = {}


def state(user_id: int | None, conversation_id: str) -> ConversationTurns:
    return _turns.setdefault((user_id, conversation_id), ConversationTurns())


def is_busy(user_id: int | None, conversation_id: str) -> bool:
    current = _turns.get((user_id, conversation_id))
    return bool(current and current.pending)


def pending_requests(user_id: int | None, conversation_id: str) -> set[str]:
    current = _turns.get((user_id, conversation_id))
    return set(current.pending) if current else set()


def active_key(user_id: int | None, conversation_id: str) -> str:
    current = _turns.get((user_id, conversation_id))
    return current.active_key if current else ""


def pending_count(user_id: int | None, conversation_id: str, session_key: str) -> int:
    current = _turns.get((user_id, conversation_id))
    return sum(key == session_key for key in current.pending.values()) if current else 0


def cancel_current(
    user_id: int | None,
    conversation_id: str,
    *,
    request_id: str | None = None,
) -> str:
    """Remember Stop, optionally only for one currently executing request.

    An exact request never falls back to a queued turn: a delayed Stop button
    belongs to the response that displayed it, even after the queue advances.
    The check and cancellation marker are synchronous, before any provider IO.
    """
    current = _turns.get((user_id, conversation_id))
    if not current or not current.pending:
        return ""
    if request_id is not None and (
        not request_id
        or current.active_request != request_id
        or request_id in current.cancelled
    ):
        return ""
    request = current.active_request or next(iter(current.pending))
    if request in current.completed:
        return ""
    current.cancelled.add(request)
    current.cancel_events.setdefault(request, asyncio.Event()).set()
    return current.pending[request]


def is_cancelled(user_id: int | None, conversation_id: str, session_key: str) -> bool:
    current = _turns.get((user_id, conversation_id))
    return bool(
        current
        and current.active_key == session_key
        and current.active_request in current.cancelled
    )


def cancel_request(
    user_id: int | None, conversation_id: str, request_id: str
) -> tuple[str, bool] | None:
    """Claim one pending request synchronously; return its key and active state."""
    current = _turns.get((user_id, conversation_id))
    if (
        current is None
        or request_id not in current.pending
        or request_id in current.cancelled
        or request_id in current.completed
    ):
        return None
    current.cancelled.add(request_id)
    current.cancel_events.setdefault(request_id, asyncio.Event()).set()
    return current.pending[request_id], current.active_request == request_id


def request_is_open(user_id: int | None, conversation_id: str, request_id: str) -> bool:
    current = _turns.get((user_id, conversation_id))
    return bool(
        current
        and request_id in current.pending
        and request_id not in current.cancelled
        and request_id not in current.completed
    )


def active_request(
    user_id: int, session_key: str, conversation_id: str | None = None
) -> tuple[str, str] | None:
    """Capture the request that owns a session when it asks for permission."""
    for (owner, queue_id), current in _turns.items():
        if (
            owner == user_id
            and (conversation_id is None or queue_id == conversation_id)
            and current.active_key == session_key
            and current.active_request
        ):
            return queue_id, current.active_request
    return None


def release(user_id: int | None, conversation_id: str, request_id: str) -> None:
    key = (user_id, conversation_id)
    current = _turns.get(key)
    if current:
        current.pending.pop(request_id, None)
        current.cancelled.discard(request_id)
        current.cancel_events.pop(request_id, None)
        current.completed.discard(request_id)
        if not current.pending and not current.lock.locked():
            _turns.pop(key, None)


def register_change_sink(sink: ChangeSink) -> None:
    if sink not in _sinks:
        _sinks.append(sink)


async def _deliver(sink: ChangeSink, change: dict) -> None:
    try:
        await asyncio.wait_for(sink(change), timeout=3)
    except Exception:
        # A failed display must never fail or replay the underlying AI turn.
        log.warning("Conversation change delivery failed", exc_info=True)


def publish(
    user_id: int | None, conversation_id: str, session_key: str, phase: str
) -> None:
    if user_id is None or not conversation_id:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Synchronous tools can write notes without a running web service.
        # The transcript remains the source of truth for the next connection.
        return
    change = {
        "user_id": user_id,
        "conversation_id": conversation_id,
        "session_key": session_key,
        "phase": phase,
        "is_busy": is_busy(user_id, conversation_id),
        "sequence": next(_sequence),
    }
    for sink in _sinks:
        task = loop.create_task(_deliver(sink, dict(change)))
        _deliveries.add(task)
        task.add_done_callback(_deliveries.discard)
