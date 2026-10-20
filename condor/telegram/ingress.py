"""Bound Telegram preparation work without serializing model responses.

A chat reserves its FIFO position before downloads, transcription or album
collection begin. The handler releases that position when the runtime accepts
its request; the runtime owns conversation ordering after that point. All state
is process-local so handler reloads cannot lose active work and persistence
never copies tasks, locks or media bytes.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator

from aiogram.types import Message

from condor.telegram.input import InputError
from condor.telegram.update import TgUpdate


@dataclass
class Admission:
    """One complete Telegram input and its short-lived preparation reservation."""

    messages: tuple[Message, ...]
    request_id: str
    accepted: bool = False
    _lock: asyncio.Lock | None = field(default=None, repr=False)

    def release(self) -> None:
        """Let the next input prepare; acceptance is set by the runtime consumer."""
        if self._lock is not None:
            self._lock.release()
            self._lock = None


@dataclass
class _Pending:
    chat_id: int
    request_id: str
    task: asyncio.Task
    messages: dict[int, Message]
    first_seen: float
    last_seen: float
    album: bool
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    sealed: bool = False
    error: InputError | None = None
    admission: Admission | None = None


@dataclass(frozen=True)
class _Recent:
    expires: float
    message_ids: frozenset[int]
    album: bool
    accepted: bool


def _late_album() -> InputError:
    return InputError(
        "late_album",
        "The album arrived in separate batches. Please resend all images together "
        "in a new album (up to 4 images).",
    )


class TelegramIngress:
    """Own bounded intake, album assembly and preparation cancellation."""

    def __init__(
        self,
        *,
        per_chat_limit: int = 4,
        global_limit: int = 16,
        album_quiet: float = 0.35,
        album_wait: float = 1.2,
        recent_limit: int = 1024,
        recent_ttl: float = 600,
    ) -> None:
        self._per_chat_limit = per_chat_limit
        self._global_limit = global_limit
        self._album_quiet = album_quiet
        self._album_wait = album_wait
        self._recent_limit = recent_limit
        self._recent_ttl = recent_ttl
        self._pending: dict[str, _Pending] = {}
        self._chat_counts: dict[int, int] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._recent: OrderedDict[str, _Recent] = OrderedDict()
        self._closed = False

    def _prune_recent(self, now: float) -> None:
        while self._recent:
            key, recent = next(iter(self._recent.items()))
            if recent.expires > now and len(self._recent) <= self._recent_limit:
                break
            self._recent.pop(key)

    async def _collect_album(self, pending: _Pending) -> None:
        while pending.error is None:
            now = time.monotonic()
            deadline = min(
                pending.last_seen + self._album_quiet,
                pending.first_seen + self._album_wait,
            )
            if now >= deadline:
                break
            pending.changed.clear()
            try:
                await asyncio.wait_for(pending.changed.wait(), deadline - now)
            except TimeoutError:
                break
        pending.sealed = True
        if pending.error is not None:
            raise pending.error

    @asynccontextmanager
    async def receive(self, update: TgUpdate) -> AsyncIterator[Admission | None]:
        """Reserve one turn, or yield ``None`` for a replay or album follower.

        Authorization and routing to configuration flows belong to the caller.
        Failed ordinary inputs can be retried. Albums retain a small
        tombstone even on cancellation so a delayed member cannot become a partial
        second request; the user must resend the album as a new message group.
        """
        if self._closed:
            raise InputError(
                "shutdown", "The bot is restarting. Please try again shortly."
            )
        message = update.message
        chat_id = update.effective_chat.id
        message_id = message.message_id
        group = getattr(message, "media_group_id", None)
        request_id = (
            f"telegram:{chat_id}:album:{group}"
            if group
            else f"telegram:{chat_id}:{message_id}"
        )
        now = time.monotonic()
        self._prune_recent(now)
        recent = self._recent.get(request_id)
        if recent is not None:
            if recent.album and (
                message_id not in recent.message_ids or not recent.accepted
            ):
                raise _late_album()
            yield None
            return
        pending = self._pending.get(request_id)
        if pending is not None:
            if message_id not in pending.messages:
                if pending.sealed:
                    raise _late_album()
                if len(pending.messages) >= 10:
                    pending.error = InputError(
                        "album_limit",
                        "The album is too large. Send up to 4 images together.",
                    )
                else:
                    pending.messages[message_id] = message
                    pending.last_seen = now
                pending.changed.set()
            yield None
            return
        if (
            len(self._pending) >= self._global_limit
            or self._chat_counts.get(chat_id, 0) >= self._per_chat_limit
        ):
            raise InputError(
                "busy",
                "Too many messages are waiting. Please wait for an answer or use /stop.",
            )

        task = asyncio.current_task()
        assert task is not None
        pending = _Pending(
            chat_id, request_id, task, {message_id: message}, now, now, bool(group)
        )
        self._pending[request_id] = pending
        self._chat_counts[chat_id] = self._chat_counts.get(chat_id, 0) + 1
        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        admission = None
        acquired = False
        try:
            # The reservation precedes album debounce as well as expensive I/O,
            # so a following text message cannot overtake an earlier image.
            await lock.acquire()
            acquired = True
            if pending.album:
                await self._collect_album(pending)
            else:
                pending.sealed = True
            admission = Admission(
                messages=tuple(
                    pending.messages[key] for key in sorted(pending.messages)
                ),
                request_id=request_id,
                _lock=lock,
            )
            pending.admission = admission
            yield admission
        finally:
            if admission is not None:
                admission.release()
            elif acquired:
                lock.release()
            accepted = admission is not None and admission.accepted
            if accepted or pending.album:
                finished = time.monotonic()
                self._recent[request_id] = _Recent(
                    finished + self._recent_ttl,
                    frozenset(pending.messages),
                    pending.album,
                    accepted,
                )
                self._prune_recent(finished)
            self._pending.pop(request_id, None)
            remaining = self._chat_counts[chat_id] - 1
            if remaining:
                self._chat_counts[chat_id] = remaining
            else:
                self._chat_counts.pop(chat_id)
                self._locks.pop(chat_id)

    def cancel(self, chat_id: int) -> bool:
        """Cancel preparation for this chat; accepted turns belong to runtime."""
        cancelled = False
        for pending in tuple(self._pending.values()):
            if pending.chat_id == chat_id and not (
                pending.admission and pending.admission.accepted
            ):
                pending.task.cancel()
                cancelled = True
        return cancelled

    async def close(self) -> None:
        """Cancel and join all receive owners before application shutdown."""
        self._closed = True
        current = asyncio.current_task()
        tasks = {
            pending.task
            for pending in tuple(self._pending.values())
            if pending.task is not current
        }
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._recent.clear()


_instance: TelegramIngress | None = None


def get_ingress() -> TelegramIngress:
    global _instance
    if _instance is None:
        _instance = TelegramIngress()
    return _instance


async def close_ingress() -> None:
    # Updates still being handled when polling stops keep running. Keep a
    # closed tombstone so those handlers cannot lazily reopen intake.
    await get_ingress().close()
