"""Chat Stop freezes its existing requests and never reaches later input."""

import asyncio
from types import SimpleNamespace

import pytest

import utils.auth as auth_module
from condor.runtime import client as runtime
from condor.telegram.output import runs
from condor.telegram.output.runs import RunTarget, register_run
from handlers.agents.stop import handle_stop_callback, stop_command

CHAT_ID = 4242
USER_ID = 7


@pytest.fixture(autouse=True)
def isolated_runs(monkeypatch):
    monkeypatch.setattr(runs, "_runs", {})


@pytest.fixture
def approved_user(monkeypatch):
    from config_manager import UserRole

    monkeypatch.setattr(
        auth_module,
        "get_config_manager",
        lambda: SimpleNamespace(get_user_role=lambda _uid: UserRole.ADMIN),
    )


def update(replies, *, token=None, message_id=19, user_id=USER_ID):
    async def answer(text="", **kwargs):
        replies.append(text)

    return SimpleNamespace(
        message=SimpleNamespace(text="/stop", answer=answer),
        effective_chat=SimpleNamespace(id=CHAT_ID, type="private"),
        effective_user=SimpleNamespace(id=user_id, username="tester"),
        callback_query=(
            SimpleNamespace(
                data=f"agent:stop_run:{token}",
                message=SimpleNamespace(message_id=message_id),
                answer=answer,
            )
            if token is not None
            else None
        ),
    )


def target(request_id, *, conversation="conversation", accepted=True):
    result = RunTarget(CHAT_ID, USER_ID, conversation, request_id, accepted=accepted)
    result.message_ids.add(19)
    register_run(result)
    return result


def test_stop_snapshots_preparing_queued_and_running_across_conversations(
    approved_user, monkeypatch
):
    current = target("current")
    queued = target("queued", conversation="another-conversation")
    preparing = target("preparing", conversation="", accepted=False)
    unrelated = RunTarget(99, USER_ID, "elsewhere", "unrelated", accepted=True)
    register_run(unrelated)
    stopped, cancelled, snapshots, replies = [], [], [], []
    for item in (current, queued, preparing):
        item.on_stop = lambda item=item: stopped.append(item.request_id)
        item.cancel_preparation = lambda item=item: cancelled.append(item.request_id)

    async def cancel_requests(requests):
        snapshots.append(tuple(requests))
        assert stopped == ["current", "queued", "preparing"]
        assert cancelled == ["preparing"]
        target("later")
        await asyncio.sleep(0)
        return 2

    monkeypatch.setattr(runtime, "cancel_requests", cancel_requests)
    asyncio.run(stop_command(update(replies), SimpleNamespace(user_data={})))

    assert snapshots == [
        (
            (USER_ID, "conversation", "current"),
            (USER_ID, "another-conversation", "queued"),
        )
    ]
    assert [item.request_id for item in runs.runs_for_chat(CHAT_ID, USER_ID)] == [
        "later"
    ]
    assert not unrelated.stopped
    assert "cleared the queue" in replies[0]


def test_repeated_old_button_does_not_stop_later_input(monkeypatch):
    first = target("first")
    queued = target("queued")
    stopped, replies = [], []

    async def cancel_requests(requests):
        stopped.extend(requests)
        target("later")
        return 2

    monkeypatch.setattr(runtime, "cancel_requests", cancel_requests)

    async def scenario():
        click = update(replies, token=first.token)
        await handle_stop_callback(click, SimpleNamespace(user_data={}))
        await handle_stop_callback(click, SimpleNamespace(user_data={}))

    asyncio.run(scenario())
    assert first.stopped and queued.stopped
    assert stopped == [
        (USER_ID, "conversation", "first"),
        (USER_ID, "conversation", "queued"),
    ]
    assert [item.request_id for item in runs.runs_for_chat(CHAT_ID, USER_ID)] == [
        "later"
    ]
    assert "already finished" in replies[-1]


@pytest.mark.parametrize("message_id,user_id", [(20, USER_ID), (19, 8)])
def test_button_requires_the_original_message_and_owner(
    monkeypatch, message_id, user_id
):
    current = target("current")
    replies = []

    async def unexpected(_requests):
        pytest.fail("an unrelated button must not cancel a request")

    monkeypatch.setattr(runtime, "cancel_requests", unexpected)
    asyncio.run(
        handle_stop_callback(
            update(
                replies, token=current.token, message_id=message_id, user_id=user_id
            ),
            SimpleNamespace(user_data={}),
        )
    )
    assert not current.stopped
    assert replies == ["This response has already finished."]


def test_stop_on_idle_chat_changes_nothing(approved_user):
    replies = []
    asyncio.run(stop_command(update(replies), SimpleNamespace(user_data={})))
    assert replies == ["Nothing to stop — the agent is idle."]


def test_stop_is_private_chat_only(approved_user):
    current, replies = target("current"), []
    event = update(replies)
    event.effective_chat.type = "supergroup"
    asyncio.run(stop_command(event, SimpleNamespace(user_data={})))
    assert not current.stopped
    assert "private chats" in replies[0]
