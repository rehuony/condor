"""Native composer stop; stale draft ids never reach the provider."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from condor.telegram.output import runs as output_runs
from condor.telegram.output.runs import RunTarget, register_run
from handlers.agents.stop import handle_stopped_generation


@pytest.fixture
def controls(monkeypatch):
    monkeypatch.setattr(output_runs, "_runs", {})
    monkeypatch.setattr(output_runs, "_by_draft", {})
    target = RunTarget(42, 42, "conversation", "request", "agent", draft_id=7)
    register_run(target)
    abort = AsyncMock(return_value=True)
    monkeypatch.setattr("condor.runtime.client.abort_turn", abort)
    return target, abort


def stopped_update(chat_id=42, draft_id=7):
    return SimpleNamespace(
        stopped_message_generation=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id),
            draft_id=draft_id,
            message_thread_id=None,
        ),
        effective_chat=SimpleNamespace(id=chat_id, type="private"),
        effective_user=SimpleNamespace(id=chat_id),
    )


@pytest.mark.asyncio
async def test_native_stop_aborts_exact_request(controls):
    target, abort = controls
    await handle_stopped_generation(stopped_update(), None)
    abort.assert_awaited_once_with(42, "conversation", "request")
    assert target.stopped
    assert output_runs.run_for_draft(42, 7) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chat_id,draft_id",
    [(43, 7), (42, 8), (42, 0)],
)
async def test_foreign_and_stale_drafts_cannot_stop(controls, chat_id, draft_id):
    _, abort = controls
    await handle_stopped_generation(stopped_update(chat_id, draft_id), None)
    abort.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_stop_is_retired_before_awaiting_provider(controls):
    target, abort = controls

    async def cancel(*args):
        await handle_stopped_generation(stopped_update(), None)

    abort.side_effect = cancel
    await handle_stopped_generation(stopped_update(), None)
    assert abort.await_count == 1
    assert target.stopped
