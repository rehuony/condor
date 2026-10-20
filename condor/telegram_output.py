"""Process-local run controls and ordered Telegram final delivery."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field

_tails: dict[int, asyncio.Future] = {}
_runs: dict[str, "RunTarget"] = {}


class OutputLease:
    """Reserve output order now, without blocking the runtime event consumer."""

    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self._previous = _tails.get(chat_id)
        self._finished = asyncio.get_running_loop().create_future()
        self._released = False
        _tails[chat_id] = self._finished

    async def acquire(self) -> None:
        if self._previous is not None:
            await asyncio.shield(self._previous)

    def release(self) -> None:
        if self._released:
            return
        self._released = True

        def finish(_=None):
            if not self._finished.done():
                self._finished.set_result(None)
            if _tails.get(self.chat_id) is self._finished:
                _tails.pop(self.chat_id, None)

        # A cancelled waiter must not let the next turn overtake its predecessor.
        if self._previous is not None and not self._previous.done():
            self._previous.add_done_callback(finish)
        else:
            finish()


@dataclass
class RunTarget:
    chat_id: int
    user_id: int | None
    conversation_id: str
    request_id: str
    agent_slug: str = ""
    token: str = field(default_factory=lambda: secrets.token_hex(8))
    approvals: list = field(default_factory=list, repr=False)
    stopped: bool = False


def register_run(target: RunTarget) -> None:
    if target.request_id and not target.stopped:
        _runs[target.token] = target


def unregister_run(target: RunTarget) -> None:
    if _runs.get(target.token) is target:
        _runs.pop(target.token, None)


def active_run(token: str, chat_id: int, user_id: int) -> RunTarget | None:
    target = _runs.get(token)
    if target and target.chat_id == chat_id and target.user_id == user_id:
        return target
    return None


def note_approval(chat_id: int, pending) -> None:
    """Attach the registry entry only to the request currently executing it.

    The UI reads its status; it cannot resolve or approve the request.
    """
    from condor.runtime import turns

    for target in tuple(_runs.values()):
        if target.chat_id != chat_id or target.user_id != pending.user_id:
            continue
        state = turns.state(target.user_id, target.conversation_id)
        if (
            state.active_key == pending.session_key
            and state.active_request == target.request_id
        ):
            target.approvals.append(pending)
