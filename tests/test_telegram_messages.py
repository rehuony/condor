"""Exercise the real PTB dispatcher and registered Telegram content router."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from telegram import Message, Update
from telegram.ext import Application, MessageHandler
from telegram.request import BaseRequest

import main
import utils.auth as auth
from config_manager import UserRole
from handlers import agents, bots


class OfflineRequest(BaseRequest):
    """Initialize a real PTB Application without contacting Telegram."""

    @property
    def read_timeout(self):
        return 1

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        assert url.endswith("/getMe"), "tests must never call Telegram"
        return (
            200,
            json.dumps(
                {
                    "ok": True,
                    "result": {
                        "id": 123,
                        "is_bot": True,
                        "first_name": "Condor",
                        "username": "condor_test",
                    },
                }
            ).encode(),
        )


def _update(app, content=None, *, kind="message", chat_type="private"):
    data = {
        "message_id": 11,
        "date": 1,
        "chat": {"id": 7, "type": chat_type},
        "from": {"id": 7, "is_bot": False, "first_name": "Tester"},
        **(content or {}),
    }
    return Update.de_json({"update_id": 20, kind: data}, app.bot)


def _photo(**kwargs):
    return {
        "photo": [
            {
                "file_id": "photo",
                "file_unique_id": "photo-unique",
                "width": 10,
                "height": 10,
            }
        ],
        **kwargs,
    }


def _document(name="chart.png", mime="image/png"):
    return {
        "document": {
            "file_id": "document",
            "file_unique_id": "document-unique",
            "file_name": name,
            "mime_type": mime,
        }
    }


def _reply_image():
    return {
        "text": "Analyze this image",
        "reply_to_message": {
            "message_id": 10,
            "date": 1,
            "chat": {"id": 7, "type": "private"},
            **_photo(),
        },
    }


@pytest_asyncio.fixture
async def dispatch(monkeypatch):
    state = SimpleNamespace(
        role=UserRole.USER,
        ai=AsyncMock(),
        reply=AsyncMock(return_value=SimpleNamespace(message_id=99)),
        upload=AsyncMock(),
        errors=[],
    )
    cm = SimpleNamespace(
        get_user_role=lambda _uid: state.role,
        register_pending=Mock(return_value=True),
    )
    monkeypatch.setattr(auth, "get_config_manager", lambda: cm)
    monkeypatch.setattr(auth, "_capture_identity", Mock())
    monkeypatch.setattr(auth, "_refresh_active_server", Mock())
    monkeypatch.setattr(auth, "_notify_admin_new_user", AsyncMock())
    monkeypatch.setattr(Message, "reply_text", state.reply)
    monkeypatch.setattr(agents, "agent_message_handler", state.ai)
    monkeypatch.setattr(bots, "handle_config_file_upload", state.upload)
    state.app = (
        Application.builder()
        .token("123:offline-test")
        .request(OfflineRequest())
        .get_updates_request(OfflineRequest())
        .build()
    )
    main.register_handlers(state.app)

    async def error_handler(update, context):
        state.errors.append(context.error)

    state.app.add_error_handler(error_handler)
    await state.app.initialize()

    async def send(content=None, **kwargs):
        update = _update(state.app, content, **kwargs)
        await state.app.process_update(update)
        assert not state.errors
        return update

    state.send = send
    state.cm = cm
    state.user_data = state.app.user_data[7]
    yield state
    await state.app.shutdown()


@pytest.mark.asyncio
async def test_obsolete_native_stop_is_not_treated_as_chat_input(dispatch, monkeypatch):
    incoming = Update.de_json(
        {
            "update_id": 21,
            "stopped_message_generation": {
                "chat": {"id": 7, "type": "private"},
                "draft_id": 10,
            },
        },
        dispatch.app.bot,
    )
    await dispatch.app.process_update(incoming)
    assert not dispatch.errors
    dispatch.ai.assert_not_awaited()
    dispatch.reply.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        {"text": "Hello"},
        _photo(),
        _photo(caption="Analyze this chart"),
        _document(),
        _document("notes.md", "text/markdown"),
        {
            "voice": {
                "file_id": "voice",
                "file_unique_id": "voice-unique",
                "duration": 2,
            }
        },
        {
            "audio": {
                "file_id": "audio",
                "file_unique_id": "audio-unique",
                "duration": 2,
            }
        },
        _reply_image(),
        {
            "video": {
                "file_id": "video",
                "file_unique_id": "video-unique",
                "width": 10,
                "height": 10,
                "duration": 2,
            }
        },
        _document("report.pdf", "application/pdf"),
        {"location": {"latitude": 1.0, "longitude": 2.0}},
    ],
    ids=[
        "text",
        "photo",
        "caption",
        "image-file",
        "text-file",
        "voice",
        "audio",
        "reply-photo",
        "unsupported-video",
        "unsupported-pdf",
        "unsupported-location",
    ],
)
async def test_all_ordinary_content_reaches_one_agent_input_boundary(dispatch, content):
    update = await dispatch.send(content)
    dispatch.ai.assert_awaited_once()
    assert dispatch.ai.await_args.args[0] is update
    assert dispatch.user_data["_user_id"] == 7
    dispatch.reply.assert_not_called()
    assert (
        sum(isinstance(handler, MessageHandler) for handler in dispatch.app.handlers[0])
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,chat_type,content",
    [
        ("edited_message", "private", _photo()),
        ("edited_message", "private", {"text": "Changed question"}),
        ("channel_post", "channel", _photo()),
        ("business_message", "private", _photo()),
        ("message", "group", _photo()),
        ("message", "supergroup", {"text": "Group text"}),
        ("message", "private", {"new_chat_title": "Service event"}),
    ],
)
async def test_edits_other_surfaces_and_service_updates_do_not_trigger_ai(
    dispatch, kind, chat_type, content
):
    await dispatch.send(content, kind=kind, chat_type=chat_type)
    dispatch.ai.assert_not_called()
    dispatch.reply.assert_not_called()
    assert "_user_id" not in dispatch.user_data


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.BLOCKED, UserRole.PENDING, None])
@pytest.mark.parametrize("content", [_photo(), _document(), {"text": "hello"}])
@pytest.mark.parametrize("chat_type", ["private", "group", "supergroup"])
async def test_auth_runs_before_ai_or_config_upload(dispatch, role, content, chat_type):
    dispatch.role = role
    dispatch.user_data["bots_state"] = "awaiting_config_upload"
    await dispatch.send(content, chat_type=chat_type)
    dispatch.ai.assert_not_called()
    dispatch.upload.assert_not_called()
    dispatch.reply.assert_awaited_once()
    assert "_user_id" not in dispatch.user_data
    assert dispatch.cm.register_pending.called is (role is None)


@pytest.mark.asyncio
async def test_commands_keep_their_own_dispatch_and_never_reach_ai(
    dispatch, monkeypatch
):
    stop = AsyncMock()
    monkeypatch.setattr(agents, "stop_command", stop)
    main.register_handlers(dispatch.app)
    await dispatch.send(
        {
            "text": "/stop",
            "entities": [{"type": "bot_command", "offset": 0, "length": 5}],
        }
    )
    stop.assert_awaited_once()
    dispatch.ai.assert_not_called()
    await dispatch.send(
        {
            "text": "/unknown",
            "entities": [{"type": "bot_command", "offset": 0, "length": 8}],
        }
    )
    dispatch.ai.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,state,target",
    [
        ("cex_state", "trade_set_amount", "handlers.cex.cex_message_handler"),
        ("dex_state", "swap_set_amount", "handlers.dex.dex_message_handler"),
        ("bots_state", "cfg_bulk_edit", "handlers.bots.bots_message_handler"),
        (
            "executors_state",
            "wizard_pair_input",
            "handlers.executors.executors_message_handler",
        ),
        (
            "awaiting_add_server_input",
            True,
            "handlers.config.servers.handle_server_input",
        ),
        ("awaiting_modify_input", True, "handlers.config.servers.handle_server_input"),
        ("awaiting_wallet_input", True, "handlers.config.gateway.handle_gateway_input"),
        ("routines_state", "editing", "handlers.routines.routines_message_handler"),
        ("routines_state", "daily_time", "handlers.routines.routines_message_handler"),
        (
            "awaiting_share_user_id",
            True,
            "handlers.config.servers.handle_share_user_id_input",
        ),
    ],
)
@pytest.mark.parametrize("chat_type", ["private", "group", "supergroup"])
async def test_existing_forms_consume_plain_text(
    dispatch, monkeypatch, key, state, target, chat_type
):
    handler = AsyncMock(return_value=True)
    monkeypatch.setattr(target, handler)
    dispatch.user_data[key] = state
    await dispatch.send({"text": "requested value"}, chat_type=chat_type)
    handler.assert_awaited_once()
    dispatch.ai.assert_not_called()


@pytest.mark.asyncio
async def test_trading_form_priority_is_preserved(dispatch, monkeypatch):
    cex, dex = AsyncMock(), AsyncMock()
    monkeypatch.setattr("handlers.cex.cex_message_handler", cex)
    monkeypatch.setattr("handlers.dex.dex_message_handler", dex)
    dispatch.user_data.update(cex_state="trade", dex_state="swap")
    await dispatch.send({"text": "requested value"})
    cex.assert_awaited_once()
    dex.assert_not_called()
    dispatch.ai.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_type", ["private", "group", "supergroup"])
async def test_unhandled_form_only_falls_through_to_private_ai(
    dispatch, monkeypatch, chat_type
):
    handler = AsyncMock(return_value=False)
    monkeypatch.setattr("handlers.executors.executors_message_handler", handler)
    dispatch.user_data["executors_state"] = "wizard_pair_input"
    await dispatch.send({"text": "a new question"}, chat_type=chat_type)
    handler.assert_awaited_once()
    assert dispatch.ai.await_count == (1 if chat_type == "private" else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        _photo(caption="100"),
        _document(),
        {
            "voice": {
                "file_id": "voice",
                "file_unique_id": "voice-unique",
                "duration": 2,
            }
        },
    ],
)
@pytest.mark.parametrize(
    "key,state",
    [
        ("cex_state", "trade"),
        ("dex_state", "swap"),
        ("bots_state", "cfg_bulk_edit"),
        ("executors_state", "wizard_pair_input"),
        ("routines_state", "editing"),
        ("awaiting_add_server_input", True),
        ("awaiting_modify_input", True),
        ("awaiting_gateway_input", True),
        ("awaiting_wallet_input", True),
        ("awaiting_network_input", True),
        ("awaiting_connector_input", True),
        ("awaiting_token_input", True),
        ("awaiting_pool_input", True),
        ("awaiting_rpc_input", True),
        ("awaiting_share_user_id", True),
    ],
)
async def test_attachment_cannot_be_silently_consumed_as_a_form_value(
    dispatch, content, key, state
):
    dispatch.user_data[key] = state
    await dispatch.send(content)
    dispatch.ai.assert_not_called()
    dispatch.reply.assert_awaited_once()
    assert "/cancel" in dispatch.reply.await_args.args[0]
    assert dispatch.user_data[key] == state


@pytest.mark.asyncio
async def test_reply_to_image_cannot_be_silently_consumed_as_form_text(
    dispatch, monkeypatch
):
    handler = AsyncMock()
    monkeypatch.setattr("handlers.cex.cex_message_handler", handler)
    dispatch.user_data["cex_state"] = "trade_set_amount"
    await dispatch.send(_reply_image())
    handler.assert_not_called()
    dispatch.ai.assert_not_called()
    dispatch.reply.assert_awaited_once()
    assert "/cancel" in dispatch.reply.await_args.args[0]
    assert dispatch.user_data["cex_state"] == "trade_set_amount"


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_type", ["private", "group", "supergroup"])
async def test_explicit_config_upload_takes_priority_over_agent_document_support(
    dispatch, chat_type
):
    dispatch.user_data["bots_state"] = "awaiting_config_upload"
    update = await dispatch.send(
        _document("config.yaml", "application/yaml"), chat_type=chat_type
    )
    dispatch.upload.assert_awaited_once()
    assert dispatch.upload.await_args.args[0] is update
    dispatch.ai.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [_photo(), {"text": "not a configuration file"}])
async def test_config_upload_prompt_does_not_accept_a_non_document(dispatch, content):
    dispatch.user_data["bots_state"] = "awaiting_config_upload"
    await dispatch.send(content)
    dispatch.upload.assert_not_called()
    dispatch.ai.assert_not_called()
    dispatch.reply.assert_awaited_once()
    assert "configuration file" in dispatch.reply.await_args.args[0]
    assert dispatch.user_data["bots_state"] == "awaiting_config_upload"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,state",
    [
        ("bots_state", "editing_config"),
        ("bots_state", "deploy_review"),
        ("bots_state", "gs_wizard"),
        ("bots_state", "pmm_wizard"),
        ("bots_state", "pv1_wizard"),
        ("executors_state", "wizard"),
        ("routines_state", "removed_custom_routine"),
    ],
)
@pytest.mark.parametrize("content", [{"text": "A new question"}, _photo()])
async def test_menu_state_does_not_swallow_normal_chat(dispatch, key, state, content):
    dispatch.user_data[key] = state
    await dispatch.send(content)
    dispatch.ai.assert_awaited_once()
    dispatch.reply.assert_not_called()
    assert dispatch.user_data[key] == state


@pytest.mark.asyncio
@pytest.mark.parametrize("handled", [True, False])
async def test_custom_routine_obeys_its_declared_state_and_handled_result(
    dispatch, monkeypatch, handled
):
    callback = AsyncMock(return_value=handled)
    routine = SimpleNamespace(message_handler=callback)
    lookup = lambda state: routine if state == "custom_input" else None
    monkeypatch.setattr("routines.base.get_routine_by_state", lookup)
    monkeypatch.setattr("handlers.routines.get_routine_by_state", lookup)
    dispatch.user_data["routines_state"] = "custom_input"
    await dispatch.send({"text": "Custom value"})
    callback.assert_awaited_once()
    assert dispatch.ai.await_count == (0 if handled else 1)


@pytest.mark.asyncio
async def test_custom_routine_text_prompt_refuses_attachments(dispatch, monkeypatch):
    callback = AsyncMock()
    monkeypatch.setattr(
        "routines.base.get_routine_by_state",
        lambda state: SimpleNamespace(message_handler=callback),
    )
    dispatch.user_data["routines_state"] = "custom_input"
    await dispatch.send(_photo())
    callback.assert_not_called()
    dispatch.ai.assert_not_called()
    assert "/cancel" in dispatch.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_routine_that_declines_text_preserves_lower_priority_form(
    dispatch, monkeypatch
):
    callback = AsyncMock(return_value=False)
    share = AsyncMock()
    routine = SimpleNamespace(message_handler=callback)
    monkeypatch.setattr("routines.base.get_routine_by_state", lambda state: routine)
    monkeypatch.setattr("handlers.routines.get_routine_by_state", lambda state: routine)
    monkeypatch.setattr("handlers.config.servers.handle_share_user_id_input", share)
    dispatch.user_data.update(
        routines_state="custom_input", awaiting_share_user_id=True
    )
    await dispatch.send({"text": "123"})
    callback.assert_awaited_once()
    share.assert_awaited_once()
    dispatch.ai.assert_not_called()


@pytest.mark.asyncio
async def test_actual_bot_form_dispatches_its_processor_and_reports_consumption(
    dispatch, monkeypatch
):
    processor = AsyncMock()
    monkeypatch.setitem(bots._TEXT_INPUT_HANDLERS, "cfg_bulk_edit", processor)
    dispatch.user_data["bots_state"] = "cfg_bulk_edit"
    update = await dispatch.send({"text": "  spread=0.01  "})
    processor.assert_awaited_once()
    assert processor.await_args.args[0] is update
    assert processor.await_args.args[2] == "spread=0.01"
    dispatch.ai.assert_not_called()


@pytest.mark.asyncio
async def test_progressive_bot_form_keeps_its_existing_update_contract(
    dispatch, monkeypatch
):
    processor = AsyncMock()
    monkeypatch.setattr(bots, "handle_deploy_progressive_input", processor)
    dispatch.user_data["bots_state"] = "deploy_progressive"
    update = await dispatch.send({"text": "20"})
    processor.assert_awaited_once()
    assert processor.await_args.args[0] is update
    assert len(processor.await_args.args) == 2
    dispatch.ai.assert_not_called()
