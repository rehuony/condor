"""Inline Stop is invalidated before provider cancellation begins."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from condor.telegram.output import runs as output_runs
from condor.telegram.output.runs import RunTarget, register_run
from handlers.agents.stop import handle_stop_callback


@pytest.fixture
def controls(monkeypatch):
    monkeypatch.setattr(output_runs, "_runs", {})
    target = RunTarget(42, 42, "conversation", "request", "agent", accepted=True)
    target.message_ids.add(7)
    register_run(target)
    abort = AsyncMock(return_value=True)
    monkeypatch.setattr("condor.runtime.client.cancel_requests", abort)
    return target, abort


def stopped_update(token, chat_id=42, message_id=7):
    return SimpleNamespace(
        callback_query=SimpleNamespace(
            data=f"agent:stop_run:{token}",
            message=SimpleNamespace(message_id=message_id),
            answer=AsyncMock(),
        ),
        effective_chat=SimpleNamespace(id=chat_id, type="private"),
        effective_user=SimpleNamespace(id=chat_id),
    )


@pytest.mark.asyncio
async def test_inline_stop_claims_exact_registered_requests(controls):
    target, abort = controls
    await handle_stop_callback(stopped_update(target.token), None)
    assert list(abort.call_args.args[0]) == [(42, "conversation", "request")]
    assert target.stopped
    assert output_runs.active_run(target.token, 42, 42) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chat_id,message_id",
    [(43, 7), (42, 8), (42, 0)],
)
async def test_foreign_and_stale_messages_cannot_stop(controls, chat_id, message_id):
    target, abort = controls
    await handle_stop_callback(stopped_update(target.token, chat_id, message_id), None)
    abort.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_stop_is_retired_before_awaiting_provider(controls):
    target, abort = controls

    async def cancel(*args):
        await handle_stop_callback(stopped_update(target.token), None)

    abort.side_effect = cancel
    await handle_stop_callback(stopped_update(target.token), None)
    assert abort.await_count == 1
    assert target.stopped
