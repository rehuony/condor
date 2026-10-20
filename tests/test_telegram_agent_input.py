"""Telegram media reaches the real runtime and its durable conversation store."""

import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message, Update
from PIL import Image

import config_manager
from condor.acp.client import PromptDone, TextChunk
from condor.runtime import SessionKey, SessionSpec, attachments
from condor.runtime import client as runtime
from condor.runtime import conversations, sessions
from condor.telegram.ingress import get_ingress
from condor.telegram.input import InputError, TelegramInputResolver
from condor.telegram.update import TgUpdate
from handlers import agents
from tests.conftest import patch_agents

USER = 4242


def picture(color="navy"):
    buffer = io.BytesIO()
    Image.new("RGB", (30, 20), color).save(buffer, format="PNG")
    return buffer.getvalue()


def photo(file_id="photo"):
    return [{"file_id": file_id, "file_unique_id": file_id, "width": 30, "height": 20}]


def update(message_id=1, **fields):
    payload = {
        "message_id": message_id,
        "date": 1700000000,
        "chat": {"id": USER, "type": "private"},
        "from": {"id": USER, "is_bot": False, "first_name": "Test"},
        **fields,
    }
    return TgUpdate.from_aiogram(
        Update.model_validate({"update_id": message_id, "message": payload})
    )


@pytest.fixture
def world(monkeypatch):
    calls = []

    class Client:
        accepts_images = True

        def __init__(self, **kwargs):
            self.alive = True

        async def start(self):
            pass

        async def prompt(self, text):
            return "ok"

        async def stop(self):
            self.alive = False

        async def prompt_stream(self, text, **kwargs):
            calls.append({"text": text, **kwargs})
            yield TextChunk(text="I can read the picture.")
            yield PromptDone(stop_reason="end_turn")

    monkeypatch.setattr("condor.acp.client.ACPClient", Client)
    monkeypatch.setattr(sessions, "_sessions", {})
    monkeypatch.setattr(sessions, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **k: []
    )
    role = [config_manager.UserRole.ADMIN]
    monkeypatch.setattr(
        config_manager,
        "get_config_manager",
        lambda: SimpleNamespace(
            _data={},
            get_accessible_servers=lambda _: [],
            get_user_role=lambda _: role[0],
        ),
    )
    status = SimpleNamespace(message_id=900, edit_text=AsyncMock())
    replies = AsyncMock(return_value=status)
    monkeypatch.setattr(Message, "answer", replies)
    monkeypatch.setattr(Message, "delete", AsyncMock())
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=status),
        send_rich_message_draft=AsyncMock(return_value=True),
        edit_message_text=AsyncMock(),
        delete_message=AsyncMock(),
    )

    async def send_rich_message(**kwargs):
        payload = dict(kwargs)
        rich = payload.pop("rich_message", None)
        payload["text"] = getattr(rich, "markdown", "") or payload.get("text", "")
        return await bot.send_message(**payload)

    bot.send_rich_message = send_rich_message
    context = SimpleNamespace(bot=bot, user_data={}, chat_data={})
    resolver = TelegramInputResolver(bot)
    downloads = AsyncMock(
        side_effect=lambda ref, remaining: picture(
            ref.file_id if ref.file_id in {"red", "blue"} else "navy"
        )
    )
    monkeypatch.setattr(resolver, "_download", downloads)
    monkeypatch.setattr("condor.telegram.input.get_resolver", lambda _: resolver)

    async def start():
        return await runtime.create_session(
            SessionSpec(
                key=str(SessionKey.telegram(USER)),
                user_id=USER,
                chat_id=USER,
                agent_key="claude-code",
            )
        )

    async def finish():
        await resolver.close()
        await runtime.destroy_all()

    return SimpleNamespace(
        calls=calls,
        role=role,
        start=start,
        finish=finish,
        resolver=resolver,
        downloads=downloads,
        context=context,
        replies=replies,
        status=status,
        client=Client,
    )


@pytest.mark.parametrize("kind", ["photo", "caption", "document", "reply"])
def test_pixels_reach_model_and_are_saved_with_the_user_turn(world, kind):
    async def run():
        info = await world.start()
        incoming = update(photo=photo())
        if kind == "caption":
            incoming = update(photo=photo(), caption="Read this chart")
        elif kind == "document":
            incoming = update(
                document={
                    "file_id": "photo",
                    "file_unique_id": "photo",
                    "file_name": "chart.png",
                    "mime_type": "image/png",
                }
            )
        elif kind == "reply":
            incoming = update(
                2,
                text="Read this chart",
                reply_to_message=incoming.message.model_dump(exclude_none=True),
            )
        await agents.agent_message_handler(incoming, world.context)
        assert len(world.calls) == 1
        assert world.calls[0]["images"][0].data == picture()
        assert world.calls[0]["text"] == (
            "Read this chart" if kind in {"caption", "reply"} else ""
        )
        turns = conversations.read_transcript(USER, info.conversation_id)
        user = next(turn for turn in turns if turn.role == "user")
        assert user.request_id == f"telegram:{USER}:{incoming.message.message_id}"
        assert len(user.attachments) == 1
        assert (
            attachments.load(USER, info.conversation_id, user.attachments[0]["id"])[0]
            == picture()
        )
        assert not any(key.startswith("_voice") for key in world.context.chat_data)
        await world.finish()

    asyncio.run(run())


def test_album_is_one_ordered_durable_turn_even_when_updates_repeat(world):
    async def run():
        await world.start()
        get_ingress()._album_quiet = 0.01
        first = update(1, photo=photo("red"), caption="Compare", media_group_id="album")
        second = update(2, photo=photo("blue"), media_group_id="album")
        await asyncio.gather(
            *(
                agents.agent_message_handler(item, world.context)
                for item in [second, first, second]
            )
        )
        assert len(world.calls) == 1
        assert world.calls[0]["text"] == "Compare"
        assert [item.data for item in world.calls[0]["images"]] == [
            picture("red"),
            picture("blue"),
        ]
        assert world.downloads.await_count == 2
        await world.finish()

    asyncio.run(run())


def test_media_replay_after_ingress_restart_skips_download_and_model(
    world, monkeypatch
):
    async def run():
        await world.start()
        incoming = update(photo=photo())
        await agents.agent_message_handler(incoming, world.context)
        monkeypatch.setattr("condor.telegram.ingress._instance", None)
        await agents.agent_message_handler(incoming, world.context)
        assert len(world.calls) == world.downloads.await_count == 1
        await world.finish()

    asyncio.run(run())


def test_one_failed_album_file_never_becomes_a_partial_prompt(world):
    async def run():
        info = await world.start()
        get_ingress()._album_quiet = 0.01
        world.downloads.side_effect = [
            picture(),
            InputError("download_failed", "Please send it again."),
        ]
        await asyncio.gather(
            *(
                agents.agent_message_handler(
                    update(i, photo=photo(str(i)), media_group_id="album"),
                    world.context,
                )
                for i in (1, 2)
            )
        )
        assert not world.calls
        assert not conversations.read_transcript(USER, info.conversation_id)
        assert not attachments.attachments_dir(USER, info.conversation_id).exists()
        assert "send it again" in world.status.edit_text.await_args.kwargs["text"]
        await world.finish()

    asyncio.run(run())


@pytest.mark.parametrize("change", ["conversation", "access"])
def test_change_while_reading_prevents_media_from_entering_wrong_session(world, change):
    async def run():
        first = await world.start()

        async def download(ref, remaining):
            if change == "access":
                world.role[0] = config_manager.UserRole.BLOCKED
            else:
                await runtime.destroy(SessionKey.telegram(USER))
                await world.start()
            return picture()

        world.downloads.side_effect = download
        await agents.agent_message_handler(update(photo=photo()), world.context)
        assert not world.calls
        assert not conversations.read_transcript(USER, first.conversation_id)
        assert not attachments.attachments_dir(USER, first.conversation_id).exists()
        await world.finish()

    asyncio.run(run())


def test_later_text_waits_for_image_preparation_but_model_queue_stays_available(world):
    async def run():
        await world.start()
        entered, ready = asyncio.Event(), asyncio.Event()

        async def download(ref, remaining):
            entered.set()
            await ready.wait()
            return picture()

        world.downloads.side_effect = download
        image_task = asyncio.create_task(
            agents.agent_message_handler(
                update(photo=photo(), caption="First"), world.context
            )
        )
        await entered.wait()
        text_task = asyncio.create_task(
            agents.agent_message_handler(update(2, text="Second"), world.context)
        )
        await asyncio.sleep(0)
        assert not world.calls
        ready.set()
        await asyncio.gather(image_task, text_task)
        assert len(world.calls) == 2
        assert "First" in world.calls[0]["text"]
        assert "Second" in world.calls[1]["text"]
        assert len(world.calls[0]["images"]) == 1
        await world.finish()

    asyncio.run(run())


def test_stop_cancels_download_without_calling_the_model(world):
    async def run():
        await world.start()
        entered = asyncio.Event()

        async def download(ref, remaining):
            entered.set()
            await asyncio.Event().wait()

        world.downloads.side_effect = download
        task = asyncio.create_task(
            agents.agent_message_handler(update(photo=photo()), world.context)
        )
        await entered.wait()
        await agents.stop_command.__wrapped__(update(2, text="/stop"), world.context)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not world.calls
        assert not get_ingress()._pending
        assert (await runtime.get_info(SessionKey.telegram(USER))).alive
        await world.finish()

    asyncio.run(run())


def test_text_only_model_receives_no_pixels_and_reports_capability_error(world):
    async def run():
        world.client.accepts_images = False
        await world.start()
        await agents.agent_message_handler(update(photo=photo()), world.context)
        assert not world.calls
        assert (
            "cannot read images"
            in world.context.bot.send_message.await_args.kwargs["text"]
        )
        await world.finish()

    asyncio.run(run())


def test_secret_in_second_album_member_is_redacted_and_its_source_deleted(
    world, monkeypatch
):
    seed = "legal winner thank year wave sausage worth useful legal winner thank yellow"
    deleted = []

    async def delete(message):
        deleted.append(message.message_id)

    monkeypatch.setattr(Message, "delete", delete)

    async def run():
        await world.start()
        get_ingress()._album_quiet = 0.01
        await asyncio.gather(
            agents.agent_message_handler(
                update(1, photo=photo("red"), media_group_id="album"), world.context
            ),
            agents.agent_message_handler(
                update(2, photo=photo("blue"), caption=seed, media_group_id="album"),
                world.context,
            ),
        )
        assert 2 in deleted
        assert seed not in world.calls[0]["text"]
        assert "[redacted: mnemonic]" in world.calls[0]["text"]
        await world.finish()

    asyncio.run(run())


def test_switch_during_secret_notice_cannot_move_media_into_new_conversation(
    world, monkeypatch
):
    from dataclasses import replace

    resolve = world.resolver.resolve

    async def resolve_with_text(*args, **kwargs):
        result = await resolve(*args, **kwargs)
        return replace(result, text="Decoded document context")

    monkeypatch.setattr(world.resolver, "resolve", resolve_with_text)

    async def notice(*args, **kwargs):
        await runtime.destroy(SessionKey.telegram(USER))
        await world.start()

    patch_agents(monkeypatch, "_handle_pasted_secrets", notice)

    async def run():
        original = await world.start()
        await agents.agent_message_handler(update(photo=photo()), world.context)
        assert not world.calls
        assert not attachments.attachments_dir(USER, original.conversation_id).exists()
        await world.finish()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["download", "unsupported"])
def test_caption_secret_is_handled_even_if_attachment_is_rejected(
    world, monkeypatch, failure
):
    seed = "legal winner thank year wave sausage worth useful legal winner thank yellow"
    deleted = []

    async def delete(message):
        deleted.append(message.message_id)

    monkeypatch.setattr(Message, "delete", delete)

    async def run():
        await world.start()
        incoming = update(photo=photo(), caption=seed)
        if failure == "download":
            world.downloads.side_effect = InputError(
                "download_failed", "Please send it again."
            )
        else:
            incoming = update(
                document={
                    "file_id": "pdf",
                    "file_unique_id": "pdf",
                    "file_name": "chart.pdf",
                    "mime_type": "application/pdf",
                },
                caption=seed,
            )
        await agents.agent_message_handler(incoming, world.context)
        assert deleted == [1]
        assert not world.calls
        await world.finish()

    asyncio.run(run())


def test_access_revoked_after_queue_acceptance_prevents_model_execution(
    world, monkeypatch
):
    async def run():
        info = await world.start()
        entered, release = asyncio.Event(), asyncio.Event()

        async def prompt_stream(self, text, **kwargs):
            world.calls.append({"text": text, **kwargs})
            if len(world.calls) == 1:
                entered.set()
                await release.wait()
            yield TextChunk(text="Done")
            yield PromptDone(stop_reason="end_turn")

        monkeypatch.setattr(world.client, "prompt_stream", prompt_stream)

        active = asyncio.create_task(
            agents.agent_message_handler(update(1, text="First"), world.context)
        )
        await entered.wait()
        queued = asyncio.create_task(
            agents.agent_message_handler(update(2, text="Second"), world.context)
        )
        for _ in range(100):
            if conversations.has_request(USER, f"telegram:{USER}:2"):
                break
            await asyncio.sleep(0)
        assert conversations.has_request(USER, f"telegram:{USER}:2")
        world.role[0] = config_manager.UserRole.BLOCKED
        release.set()
        await asyncio.gather(active, queued)
        assert len(world.calls) == 1
        assert "Second" not in conversations.replay_context(USER, info.conversation_id)
        await world.finish()

    asyncio.run(run())


def test_revocation_while_text_waits_for_download_prevents_both_turns(world):
    async def run():
        await world.start()
        entered, ready = asyncio.Event(), asyncio.Event()

        async def download(ref, remaining):
            entered.set()
            await ready.wait()
            return picture()

        world.downloads.side_effect = download
        image_task = asyncio.create_task(
            agents.agent_message_handler(update(photo=photo()), world.context)
        )
        await entered.wait()
        text_task = asyncio.create_task(
            agents.agent_message_handler(update(2, text="Second"), world.context)
        )
        await asyncio.sleep(0)
        world.role[0] = config_manager.UserRole.BLOCKED
        ready.set()
        await asyncio.gather(image_task, text_task)
        assert not world.calls
        await world.finish()

    asyncio.run(run())


def test_cancelled_accepted_turn_keeps_its_recorded_image(world, monkeypatch):
    entered = asyncio.Event()

    async def prompt_stream(self, text, **kwargs):
        entered.set()
        await asyncio.Event().wait()
        yield TextChunk(text="unreachable")

    monkeypatch.setattr(world.client, "prompt_stream", prompt_stream)

    async def run():
        info = await world.start()
        task = asyncio.create_task(
            agents.agent_message_handler(update(photo=photo()), world.context)
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        user = next(
            turn
            for turn in conversations.read_transcript(USER, info.conversation_id)
            if turn.role == "user"
        )
        assert (
            attachments.load(USER, info.conversation_id, user.attachments[0]["id"])[0]
            == picture()
        )
        assert not get_ingress()._pending
        await world.finish()

    asyncio.run(run())


def test_partial_save_failure_cleans_unaccepted_files(world, monkeypatch):
    save = attachments.save
    saved = []

    def fail_second(*args):
        if saved:
            raise OSError("disk full")
        result = save(*args)
        saved.append(result)
        return result

    monkeypatch.setattr(attachments, "save", fail_second)

    async def run():
        info = await world.start()
        get_ingress()._album_quiet = 0.01
        await asyncio.gather(
            *(
                agents.agent_message_handler(
                    update(i, photo=photo(str(i)), media_group_id="album"),
                    world.context,
                )
                for i in (1, 2)
            )
        )
        assert not world.calls
        assert not conversations.read_transcript(USER, info.conversation_id)
        assert (
            list(attachments.attachments_dir(USER, info.conversation_id).iterdir())
            == []
        )
        assert "Could not save" in world.status.edit_text.await_args.kwargs["text"]
        await world.finish()

    asyncio.run(run())


def test_late_runtime_duplicate_removes_only_new_unreferenced_attachments(
    world, monkeypatch
):
    from condor.runtime.events import RuntimeEvent

    async def duplicate(*args, **kwargs):
        yield RuntimeEvent.done("duplicate")

    monkeypatch.setattr(runtime, "prompt", duplicate)

    async def run():
        info = await world.start()
        existing = attachments.save(USER, info.conversation_id, picture())
        await agents.agent_message_handler(update(photo=photo()), world.context)
        assert not world.calls
        assert [
            item.name
            for item in attachments.attachments_dir(
                USER, info.conversation_id
            ).iterdir()
        ] == [existing.id]
        assert not conversations.read_transcript(USER, info.conversation_id)
        await world.finish()

    asyncio.run(run())


def test_reset_uses_own_text_even_when_reply_contains_media(world):
    async def run():
        await world.start()
        await agents.agent_message_handler(
            update(
                2,
                text="-",
                reply_to_message=update(photo=photo()).message.model_dump(
                    exclude_none=True
                ),
            ),
            world.context,
        )
        assert not world.downloads.called
        assert not world.calls
        assert await runtime.get_info(SessionKey.telegram(USER)) is None
        assert "Session reset" in world.replies.await_args.args[0]
        await world.finish()

    asyncio.run(run())


def test_shutdown_rejects_late_ptb_updates_without_reopening_intake(world):
    from condor.telegram.ingress import close_ingress

    async def run():
        await world.start()
        await close_ingress()
        await agents.agent_message_handler(update(photo=photo()), world.context)
        assert not world.downloads.called
        assert not world.calls
        assert get_ingress()._closed
        assert "restarting" in world.replies.await_args.kwargs["text"]
        await world.finish()

    asyncio.run(run())
