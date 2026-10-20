"""Process-local registry of the runs a chat can still stop or approve."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field

_runs: dict[str, "RunTarget"] = {}
_by_draft: dict[tuple[int, int], "RunTarget"] = {}
_draft_seq = 0


def allocate_draft_id() -> int:
    """A non-zero int unique in this process, reused for every update of a turn."""
    global _draft_seq
    _draft_seq += 1
    return _draft_seq


@dataclass
class RunTarget:
    chat_id: int
    user_id: int | None
    conversation_id: str
    request_id: str
    agent_slug: str = ""
    token: str = field(default_factory=lambda: secrets.token_hex(8))
    draft_id: int = 0
    approvals: list = field(default_factory=list, repr=False)
    stopped: bool = False
    nudge: Callable[[], None] | None = field(default=None, repr=False)


def register_run(target: RunTarget) -> None:
    if target.request_id and not target.stopped:
        _runs[target.token] = target
        if target.draft_id:
            _by_draft[(target.chat_id, target.draft_id)] = target


def unregister_run(target: RunTarget) -> None:
    if _runs.get(target.token) is target:
        _runs.pop(target.token, None)
    key = (target.chat_id, target.draft_id)
    if target.draft_id and _by_draft.get(key) is target:
        _by_draft.pop(key, None)


def active_run(token: str, chat_id: int, user_id: int) -> RunTarget | None:
    target = _runs.get(token)
    if target and target.chat_id == chat_id and target.user_id == user_id:
        return target
    return None


def run_for_draft(chat_id: int, draft_id: int) -> RunTarget | None:
    """The live run that owns this native-stop draft, if any."""
    return _by_draft.get((chat_id, draft_id))


def bump_chat(chat_id: int) -> None:
    """A non-draft message went to this chat; restore any live drafts now."""
    for target in tuple(_runs.values()):
        if target.chat_id == chat_id and callable(target.nudge):
            target.nudge()


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
