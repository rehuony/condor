"""Telegram intake preserves ordering while keeping stop and streaming concurrent."""

import asyncio
from types import SimpleNamespace

import pytest

from condor.telegram import ingress as telegram_ingress
from condor.telegram.ingress import TelegramIngress
from condor.telegram.input import InputError


def update(message_id, *, chat_id=7, album=None):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        message=SimpleNamespace(message_id=message_id, media_group_id=album),
    )


async def accepted(ingress, message):
    async with ingress.receive(message) as admission:
        if admission:
            admission.accepted = True
            admission.release()
        return admission


@pytest.mark.asyncio
async def test_album_members_merge_in_message_order_before_fifo_release():
    ingress = TelegramIngress(album_quiet=0.02, album_wait=0.1)
    first = asyncio.create_task(accepted(ingress, update(3, album="photos")))
    await asyncio.sleep(0)
    assert await accepted(ingress, update(1, album="photos")) is None
    assert await accepted(ingress, update(2, album="photos")) is None
    result = await first
    assert [message.message_id for message in result.messages] == [1, 2, 3]
    assert result.request_id == "telegram:7:album:photos"
    assert not ingress._pending
    assert not ingress._locks


@pytest.mark.asyncio
async def test_text_waits_for_earlier_image_preparation_but_not_its_model_response():
    ingress = TelegramIngress(album_quiet=0.02)
    prepared = asyncio.Event()
    release = asyncio.Event()
    finish = asyncio.Event()
    text_prepared = asyncio.Event()

    async def image():
        async with ingress.receive(update(1, album="photos")) as admission:
            prepared.set()
            await release.wait()
            admission.accepted = True
            admission.release()
            await finish.wait()

    async def text():
        async with ingress.receive(update(2)) as admission:
            text_prepared.set()
            admission.accepted = True
            admission.release()

    first = asyncio.create_task(image())
    await asyncio.sleep(0)
    second = asyncio.create_task(text())
    await prepared.wait()
    assert not text_prepared.is_set()
    release.set()
    await asyncio.wait_for(text_prepared.wait(), 1)
    assert not first.done()
    finish.set()
    await asyncio.gather(first, second)


@pytest.mark.asyncio
async def test_other_chat_can_prepare_while_first_chat_waits():
    ingress = TelegramIngress()
    async with ingress.receive(update(1)):
        result = await asyncio.wait_for(accepted(ingress, update(1, chat_id=8)), 1)
        assert result.request_id == "telegram:8:1"


@pytest.mark.asyncio
async def test_duplicates_are_suppressed_during_preparation_and_after_acceptance():
    ingress = TelegramIngress()
    async with ingress.receive(update(1)) as admission:
        assert await accepted(ingress, update(1)) is None
        admission.accepted = True
        admission.release()
        assert await accepted(ingress, update(1)) is None
    assert await accepted(ingress, update(1)) is None


@pytest.mark.asyncio
async def test_releasing_a_failed_turn_does_not_prevent_retry():
    ingress = TelegramIngress()
    async with ingress.receive(update(1)) as admission:
        admission.release()
        assert not admission.accepted
    assert await accepted(ingress, update(1)) is not None


@pytest.mark.asyncio
async def test_admission_limits_count_turns_and_ignore_duplicate_members():
    ingress = TelegramIngress(per_chat_limit=1, global_limit=2)
    async with ingress.receive(update(1)) as first:
        first.release()
        assert await accepted(ingress, update(1)) is None
        with pytest.raises(InputError) as error:
            await accepted(ingress, update(2))
        assert error.value.code == "busy"
        async with ingress.receive(update(1, chat_id=8)) as second:
            second.release()
            with pytest.raises(InputError) as error:
                await accepted(ingress, update(1, chat_id=9))
            assert error.value.code == "busy"
    assert await accepted(ingress, update(2)) is not None


@pytest.mark.asyncio
async def test_late_album_member_is_rejected_before_and_after_owner_finishes():
    ingress = TelegramIngress(album_quiet=0)
    async with ingress.receive(update(1, album="photos")) as admission:
        admission.accepted = True
        admission.release()
        assert await accepted(ingress, update(1, album="photos")) is None
        with pytest.raises(InputError) as error:
            await accepted(ingress, update(2, album="photos"))
        assert error.value.code == "late_album"
    assert await accepted(ingress, update(1, album="photos")) is None
    with pytest.raises(InputError) as error:
        await accepted(ingress, update(2, album="photos"))
    assert error.value.code == "late_album"


@pytest.mark.asyncio
async def test_failed_sealed_album_requires_a_new_album_instead_of_partial_retry():
    ingress = TelegramIngress(album_quiet=0)
    async with ingress.receive(update(1, album="photos")):
        pass
    for message_id in (1, 2):
        with pytest.raises(InputError) as error:
            await accepted(ingress, update(message_id, album="photos"))
        assert error.value.code == "late_album"
    assert await accepted(ingress, update(3, album="resend")) is not None


@pytest.mark.asyncio
async def test_album_over_telegram_limit_rejects_owner_without_delivering_a_subset():
    ingress = TelegramIngress(album_quiet=0.02, album_wait=0.1)
    first = asyncio.create_task(accepted(ingress, update(1, album="photos")))
    await asyncio.sleep(0)
    for message_id in range(2, 12):
        assert await accepted(ingress, update(message_id, album="photos")) is None
    with pytest.raises(InputError) as error:
        await first
    assert error.value.code == "album_limit"
    assert not ingress._pending
    assert not ingress._locks


@pytest.mark.asyncio
async def test_album_hard_deadline_caps_a_continuously_extending_quiet_period():
    ingress = TelegramIngress(album_quiet=0.05, album_wait=0.055)
    first = asyncio.create_task(accepted(ingress, update(1, album="photos")))
    await asyncio.sleep(0.02)
    await accepted(ingress, update(2, album="photos"))
    await asyncio.sleep(0.02)
    await accepted(ingress, update(3, album="photos"))
    result = await asyncio.wait_for(first, 0.04)
    assert len(result.messages) == 3


@pytest.mark.asyncio
async def test_stop_cancels_preparation_and_waiters_but_keeps_accepted_owner():
    ingress = TelegramIngress()
    streaming = asyncio.Event()
    preparing = asyncio.Event()
    finish = asyncio.Event()

    async def stream():
        async with ingress.receive(update(1)) as admission:
            admission.accepted = True
            admission.release()
            streaming.set()
            await finish.wait()

    async def prepare():
        async with ingress.receive(update(2)):
            preparing.set()
            await asyncio.Event().wait()

    first = asyncio.create_task(stream())
    await streaming.wait()
    second = asyncio.create_task(prepare())
    await preparing.wait()
    third = asyncio.create_task(accepted(ingress, update(3)))
    await asyncio.sleep(0)
    assert ingress.cancel(7)
    for task in (second, third):
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not first.done()
    assert not ingress.cancel(7)
    assert await accepted(ingress, update(4)) is not None
    finish.set()
    await first
    assert not ingress._pending
    assert not ingress._locks


@pytest.mark.asyncio
async def test_cancelling_waiter_does_not_unlock_another_preparation():
    ingress = TelegramIngress()
    async with ingress.receive(update(1)):
        waiter = asyncio.create_task(accepted(ingress, update(2)))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert ingress._locks[7].locked()
    assert await accepted(ingress, update(3)) is not None


@pytest.mark.asyncio
async def test_close_joins_owned_tasks_and_prevents_new_admissions():
    ingress = TelegramIngress(album_quiet=10, album_wait=10)
    first = asyncio.create_task(accepted(ingress, update(1, album="photos")))
    await asyncio.sleep(0)
    second = asyncio.create_task(accepted(ingress, update(2)))
    await asyncio.sleep(0)
    await ingress.close()
    assert first.cancelled() and second.cancelled()
    assert not ingress._pending
    assert not ingress._locks
    assert not ingress._chat_counts
    with pytest.raises(InputError) as error:
        await accepted(ingress, update(3))
    assert error.value.code == "shutdown"


@pytest.mark.asyncio
async def test_recent_cache_is_bounded_and_expires(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(
        telegram_ingress, "time", SimpleNamespace(monotonic=lambda: now[0])
    )
    ingress = TelegramIngress(recent_limit=2, recent_ttl=10)
    for message_id in (1, 2, 3):
        await accepted(ingress, update(message_id))
    assert len(ingress._recent) == 2
    assert "telegram:7:1" not in ingress._recent
    assert await accepted(ingress, update(2)) is None
    now[0] += 11
    assert await accepted(ingress, update(2)) is not None
    assert len(ingress._recent) == 1


@pytest.mark.asyncio
async def test_singleton_has_an_explicit_shutdown_owner(monkeypatch):
    monkeypatch.setattr(telegram_ingress, "_instance", None)
    first = telegram_ingress.get_ingress()
    assert telegram_ingress.get_ingress() is first
    await telegram_ingress.close_ingress()
    assert first._closed
    assert telegram_ingress.get_ingress() is first


@pytest.mark.asyncio
async def test_cancelled_album_does_not_restart_from_a_delayed_member():
    ingress = TelegramIngress(album_quiet=10, album_wait=10)
    first = asyncio.create_task(accepted(ingress, update(1, album="photos")))
    await asyncio.sleep(0)
    assert ingress.cancel(7)
    with pytest.raises(asyncio.CancelledError):
        await first
    with pytest.raises(InputError) as error:
        await accepted(ingress, update(2, album="photos"))
    assert error.value.code == "late_album"
    assert await accepted(ingress, update(3)) is not None
