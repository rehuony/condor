"""Generated images reach Telegram and the same durable, private conversation."""

import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from aiohttp import ClientConnectorError
from fastapi import HTTPException
from PIL import Image
from telegram.error import NetworkError

from condor import notifications
from condor.runtime import attachments, conversations, turns
from condor.runtime.models import SessionInfo
from condor.web.models import WebUser
from condor.web.routes import agents, code
from mcp_servers.condor.exceptions import APIError
from mcp_servers.condor.tools import notification as tool

USER = WebUser(id=1, role="user")
SESSION = "tg:1:condor"


class Bot:
    def __init__(self):
        self.photos = []
        self.messages = []
        self.photo_error = None
        self.text_error = None

    async def send_photo(self, **kwargs):
        self.photos.append({**kwargs, "photo": kwargs["photo"].read()})
        if self.photo_error:
            raise self.photo_error
        return {"ok": True, "result": {"message_id": 12}}

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)
        if self.text_error:
            raise self.text_error
        return {"ok": True, "result": {"message_id": 13}}

    async def get_chat_member(self, **kwargs):
        return SimpleNamespace(status="left")


@pytest.fixture
def setup(monkeypatch, tmp_path):
    conv = conversations.new_conversation(USER.id, "tg", agent_key="claude-code")
    cm = SimpleNamespace(
        is_admin=lambda uid: False,
        get_user_preference=lambda uid, key, default: key == "code_run",
        get_user=lambda uid: {"id": uid},
    )
    monkeypatch.setattr("config_manager.get_config_manager", lambda: cm)
    monkeypatch.setattr(code, "get_config_manager", lambda: cm)
    monkeypatch.setattr(notifications, "_push_sinks", [])
    monkeypatch.setattr(turns, "_sinks", [])
    bot = Bot()
    monkeypatch.setattr("condor.agents.delegate.resolve_bot", lambda b=None: bot)

    async def info(key):
        return SessionInfo(
            key=str(key),
            agent_key="claude-code",
            user_id=USER.id,
            conversation_id=conv.id,
        )

    monkeypatch.setattr("condor.runtime.client.get_info", info)
    buf = BytesIO()
    Image.new("RGB", (64, 40), "navy").save(buf, "PNG")
    image = tmp_path / "plan.png"
    image.write_bytes(buf.getvalue())
    return SimpleNamespace(conv=conv, bot=bot, image=image, data=buf.getvalue(), cm=cm)


def request(setup, **kwargs):
    values = {
        "text": "SOL plan",
        "chat_id": USER.id,
        "session_key": SESSION,
        "image_path": str(setup.image),
    }
    values.update(kwargs)
    return agents.NotifyRequest(**values)


def run(req):
    return asyncio.run(agents.notify_user(req, user=USER))


def test_image_and_long_text_are_delivered_and_persisted_without_local_paths(setup):
    body = "行情 📈 " * 1500
    result = run(request(setup, text=body))
    assert result["sent"] and result["image_sent"] and result["text_sent"]
    assert result["recorded"] and result["image_recorded"]
    assert len(setup.bot.photos) == 1
    assert setup.bot.photos[0]["photo"] == setup.data
    assert not setup.bot.photos[0].get("caption")
    assert len(setup.bot.messages) > 1
    assert "".join(m["text"] for m in setup.bot.messages) == body
    for message in setup.bot.messages:
        assert len(message["text"].encode("utf-16-le")) // 2 <= 4096
    (note,) = conversations.read_transcript(USER.id, setup.conv.id)
    assert (note.role, note.kind, note.text) == ("system", "notification", body)
    assert note.attachments == [result["attachment"]]
    assert str(setup.image) not in note.model_dump_json()
    assert (
        attachments.load(USER.id, setup.conv.id, note.attachments[0]["id"])[0]
        == setup.data
    )
    (bell,) = notifications.list_for(USER.id)
    assert bell.link == f"/?conversation={setup.conv.id}"


def test_image_only_notification_publishes_the_conversation_change(setup, monkeypatch):
    changes = []

    async def sink(change):
        changes.append(change)

    monkeypatch.setattr(turns, "_sinks", [sink])

    async def drive():
        result = await agents.notify_user(request(setup, text=""), user=USER)
        await asyncio.gather(*list(turns._deliveries))
        return result

    result = asyncio.run(drive())
    assert result["sent"] and result["image_sent"] and not result["text_sent"]
    assert setup.bot.messages == []
    assert changes[-1]["conversation_id"] == setup.conv.id
    assert changes[-1]["phase"] == "completed"
    assert conversations.read_transcript(USER.id, setup.conv.id)[0].attachments
    replay = conversations.replay_context(USER.id, setup.conv.id)
    assert f"conversation={setup.conv.id}" in replay
    assert f"attachment={result['attachment']['id']}" in replay
    assert "content not included in replay" in replay


def test_local_mode_retains_the_actual_image_and_one_bell_entry(setup, monkeypatch):
    monkeypatch.setattr(
        "condor.agents.delegate.resolve_bot", lambda b=None: notifications.NotifyBot()
    )
    result = run(request(setup, text=""))
    assert result["sent"] is False and result["image_sent"] is False
    assert result["image_recorded"] is True
    assert "Telegram" in result["error"]
    assert len(notifications.list_for(USER.id)) == 1
    assert setup.bot.photos == []
    assert (
        attachments.load(USER.id, setup.conv.id, result["attachment"]["id"])[0]
        == setup.data
    )


def test_web_only_session_stores_image_without_telegram_delivery(setup):
    result = run(request(setup, chat_id=0))
    assert result["image_recorded"] and result["recorded"]
    assert not result["sent"] and not result["image_sent"]
    assert setup.bot.photos == []


def test_no_session_can_send_but_does_not_claim_image_history(setup):
    result = run(request(setup, session_key=""))
    assert result["image_sent"] and result["sent"]
    assert not result["image_recorded"]
    assert "No active conversation" in result["image_error"]
    assert "attachment" not in result


def test_missing_telegram_and_session_never_claims_image_delivery(setup, monkeypatch):
    monkeypatch.setattr(
        "condor.agents.delegate.resolve_bot", lambda b=None: notifications.NotifyBot()
    )
    result = run(request(setup, session_key="", text=""))
    assert not result["sent"] and not result["image_recorded"]
    assert result["error"] and result["image_error"]


@pytest.mark.parametrize("failure", ["save", "transcript"])
def test_storage_failure_does_not_prevent_telegram_and_is_reported(
    setup, monkeypatch, failure
):
    def fail(*args, **kwargs):
        raise OSError("disk full")

    if failure == "save":
        monkeypatch.setattr(attachments, "save", fail)
    else:
        # append_turn normally swallows output errors; media recording must be durable.
        from pathlib import Path

        original = Path.open

        def fail_transcript(path, *args, **kwargs):
            if path.name == "transcript.jsonl":
                raise OSError("disk full")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fail_transcript)
    result = run(request(setup))
    assert result["sent"] and result["image_sent"]
    assert not result["image_recorded"]
    assert "saved" in result["image_error"]


@pytest.mark.parametrize("foreign_admin", [False, True])
def test_foreign_session_is_refused_before_read_even_for_admin(
    setup, monkeypatch, foreign_admin
):
    async def foreign(key):
        return SessionInfo(
            key=str(key), agent_key="claude-code", user_id=2, conversation_id="other"
        )

    monkeypatch.setattr("condor.runtime.client.get_info", foreign)
    setup.cm.is_admin = lambda uid: foreign_admin
    monkeypatch.setattr(
        "condor.telegram_media.load_photo",
        lambda path: pytest.fail("unauthorized read"),
    )
    with pytest.raises(HTTPException) as error:
        run(request(setup))
    assert error.value.status_code == 403
    assert not setup.bot.photos and not notifications.list_for(USER.id)


def test_image_path_requires_code_run_grant_before_read(setup, monkeypatch):
    setup.cm.get_user_preference = lambda *args: False
    monkeypatch.setattr(
        "condor.telegram_media.load_photo",
        lambda path: pytest.fail("unauthorized read"),
    )
    with pytest.raises(HTTPException) as error:
        run(request(setup))
    assert error.value.status_code == 403
    assert not setup.bot.photos


def test_foreign_chat_is_refused_before_file_read(setup, monkeypatch):
    monkeypatch.setattr(
        "condor.telegram_media.load_photo",
        lambda path: pytest.fail("unauthorized read"),
    )
    with pytest.raises(HTTPException) as error:
        run(request(setup, chat_id=-200))
    assert error.value.status_code == 403
    assert not setup.bot.photos


def test_invalid_image_is_rejected_before_recording_or_sending(setup):
    setup.image.write_text("not an image")
    with pytest.raises(HTTPException) as error:
        run(request(setup))
    assert error.value.status_code == 400
    assert conversations.read_transcript(USER.id, setup.conv.id) == []
    assert not setup.bot.photos and not notifications.list_for(USER.id)


def test_photo_failure_preserves_text_and_saved_image_without_retry(setup):
    setup.bot.photo_error = NetworkError("upload response lost")
    result = run(request(setup))
    assert not result["sent"] and not result["image_sent"]
    assert result["text_sent"] and result["image_recorded"]
    assert "upload response lost" in result["error"]
    assert len(setup.bot.photos) == 1 and len(setup.bot.messages) == 1


def test_text_failure_after_image_does_not_replay_photo_through_mcp(setup, monkeypatch):
    setup.bot.text_error = NetworkError("text response lost")

    async def main(method, path, body, **kwargs):
        return await agents.notify_user(agents.NotifyRequest(**body), user=USER)

    monkeypatch.setattr(tool, "call_main_api", main)
    monkeypatch.setattr(tool.settings, "session_key", SESSION)
    monkeypatch.setattr(tool.settings, "chat_id", USER.id)
    monkeypatch.setattr(tool, "_send_direct", lambda *a: pytest.fail("must not replay"))
    result = asyncio.run(tool.send_notification("Plan", image_path=str(setup.image)))
    assert not result["sent"] and result["image_sent"] and not result["text_sent"]
    assert result["image_recorded"]
    assert len(setup.bot.photos) == 1


@pytest.mark.parametrize("connection_failed", [False, True])
def test_media_never_bypasses_main_process_authorization_on_failure(
    setup, monkeypatch, connection_failed
):
    async def fail(*args, **kwargs):
        if connection_failed:
            raise APIError("offline") from ClientConnectorError(
                None, ConnectionRefusedError()
            )
        raise APIError("timeout")

    monkeypatch.setattr(tool, "call_main_api", fail)
    monkeypatch.setattr(tool, "_send_direct", lambda *a: pytest.fail("must not bypass"))
    result = asyncio.run(tool.send_notification(image_path=str(setup.image)))
    assert result["sent"] is False
    assert "not replayed" in result["error"]
    assert "image_sent" not in result  # An unknown receipt is not proof of no send.


def test_stored_image_uses_existing_owner_checks_and_chat_deletion(setup):
    result = run(request(setup))
    image_id = result["attachment"]["id"]
    with pytest.raises(attachments.NotFoundError):
        attachments.load(2, setup.conv.id, image_id)
    conversations.delete_conversation(USER.id, setup.conv.id)
    with pytest.raises(attachments.NotFoundError):
        attachments.load(USER.id, setup.conv.id, image_id)


def test_http_notification_history_and_private_image_download(setup):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from condor.web.auth import get_current_user
    from condor.web.routes import conversations as conversation_routes

    app = FastAPI()
    app.include_router(agents.router)
    app.include_router(conversation_routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    with TestClient(app) as client:
        response = client.post("/agents/notify", json=request(setup).model_dump())
        assert response.status_code == 200
        result = response.json()
        assert result["sent"] and result["image_recorded"]
        url = f"/conversations/{setup.conv.id}"
        history = client.get(url).json()
        assert history["turns"][0]["attachments"] == [result["attachment"]]
        image_url = f"{url}/attachments/{result['attachment']['id']}"
        image = client.get(image_url)
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"
        assert image.content == setup.data
        app.dependency_overrides[get_current_user] = lambda: WebUser(id=2, role="user")
        assert client.get(image_url).status_code == 404
        assert client.get(image_url + "?user_id=1").status_code == 403
