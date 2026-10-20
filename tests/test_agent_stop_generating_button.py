"""Run-bound inline Stop; synthetic callbacks never reach trading services."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from condor import telegram_output
from condor.telegram_output import RunTarget, register_run
from config_manager import UserRole
from handlers import agents
from handlers.agents.menu import stop_generating_keyboard


@pytest.fixture
def controls(monkeypatch):
    monkeypatch.setattr(telegram_output, "_runs", {})
    cm = SimpleNamespace(get_user_role=lambda _: UserRole.USER)
    monkeypatch.setattr("utils.auth.get_config_manager", lambda: cm)
    target = RunTarget(42, 42, "conversation", "request", "agent")
    register_run(target)
    abort = AsyncMock(return_value=True)
    destroy = AsyncMock()
    monkeypatch.setattr(agents.runtime, "abort_turn", abort)
    monkeypatch.setattr(agents.runtime, "destroy", destroy)
    return target, abort, destroy, cm


def update(target, *, chat_id=42, user_id=42, chat_type="private", data=None):
    return SimpleNamespace(
        callback_query=SimpleNamespace(
            data=data or f"agent:cancel:{target.token}",
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        ),
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        effective_user=SimpleNamespace(id=user_id, username="synthetic"),
        message=None,
    )


@pytest.mark.asyncio
async def test_button_aborts_exact_request_and_keeps_session(controls):
    target, abort, destroy, _ = controls
    keyboard = stop_generating_keyboard(target.token)
    assert (
        keyboard.inline_keyboard[0][0].callback_data == f"agent:cancel:{target.token}"
    )
    assert len(keyboard.inline_keyboard[0][0].callback_data.encode()) <= 64
    incoming = update(target)
    await agents.agent_callback_handler(incoming, SimpleNamespace(user_data={}))
    abort.assert_awaited_once_with(42, "conversation", "request")
    destroy.assert_not_awaited()
    incoming.callback_query.message.edit_text.assert_not_awaited()
    assert not telegram_output._runs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields",
    [
        {"chat_id": 43},
        {"user_id": 43},
        {"chat_type": "group"},
        {"data": "agent:cancel:stale"},
        {"data": "agent:cancel"},
    ],
)
async def test_foreign_stale_and_old_unbound_controls_cannot_stop(controls, fields):
    target, abort, _, _ = controls
    await agents.agent_callback_handler(
        update(target, **fields), SimpleNamespace(user_data={})
    )
    abort.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_access_cannot_stop(controls):
    target, abort, _, cm = controls
    cm.get_user_role = lambda _: UserRole.BLOCKED
    await agents.agent_callback_handler(update(target), SimpleNamespace(user_data={}))
    abort.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_stop_is_retired_before_awaiting_provider(controls):
    target, abort, _, _ = controls

    async def cancel(*args):
        await agents.agent_callback_handler(
            update(target), SimpleNamespace(user_data={})
        )

    abort.side_effect = cancel
    await agents.agent_callback_handler(update(target), SimpleNamespace(user_data={}))
    assert abort.await_count == 1
