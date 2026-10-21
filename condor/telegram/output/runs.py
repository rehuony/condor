"""Process-local registry of the runs a chat can still stop or approve."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field

_runs: dict[str, "RunTarget"] = {}


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
    accepted: bool = False
    message_ids: set[int] = field(default_factory=set)
    on_stop: Callable[[], None] | None = field(default=None, repr=False)
    changed: Callable[[], None] | None = field(default=None, repr=False)
    cancel_preparation: Callable[[], None] | None = field(default=None, repr=False)


def register_run(target: RunTarget) -> None:
    if target.request_id and not target.stopped:
        _runs[target.token] = target


def unregister_run(target: RunTarget) -> None:
    if _runs.get(target.token) is target:
        _runs.pop(target.token, None)


def active_run(token: str, chat_id: int, user_id: int) -> RunTarget | None:
    target = _runs.get(token)
    if (
        target
        and not target.stopped
        and target.chat_id == chat_id
        and target.user_id == user_id
    ):
        return target
    return None


def runs_for_chat(chat_id: int, user_id: int) -> tuple[RunTarget, ...]:
    """Freeze the live requests present when a chat Stop is received."""
    return tuple(
        target
        for target in _runs.values()
        if target.chat_id == chat_id
        and target.user_id == user_id
        and not target.stopped
    )


def note_approval(chat_id: int, pending) -> None:
    """Attach the registry entry only to the request currently executing it.

    The UI reads its status; it cannot resolve or approve the request.
    """
    for target in tuple(_runs.values()):
        if (
            target.chat_id == chat_id
            and target.user_id == pending.user_id
            and target.conversation_id == pending.conversation_id
            and target.request_id == pending.request_id
            and not target.stopped
        ):
            target.approvals.append(pending)
            if target.changed is not None:
                target.changed()
                pending.add_done_callback(target.changed)
