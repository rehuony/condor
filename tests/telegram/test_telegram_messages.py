"""Exercise the real aiogram dispatcher and registered Telegram content router."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from aiogram.types import Message

import utils.auth as auth
from config_manager import UserRole
from handlers import agents, bots
from handlers.messages import INPUT_CHAT_KEY, track_input_chat
from handlers.routes import register_handlers
from tests.conftest import patch_agents
from tests.support.telegram_offline import message_update, offline_app, update_from


def _update(app, content=None, *, kind="message", chat_type="private", chat_id=7):
    return message_update(
        app.bot, content, kind=kind, chat_type=chat_type, chat_id=chat_id
    )


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


def _rich(text):
    return {"rich_message": {"blocks": [{"type": "paragraph", "text": text}]}}


def _rich_photo_block():
    return {"type": "photo", "photo": _photo()["photo"]}


@pytest_asyncio.fixture
async def dispatch(monkeypatch, tmp_path):
    state = SimpleNamespace(
        role=UserRole.USER,
        ai=AsyncMock(),
        reply=AsyncMock(return_value=SimpleNamespace(message_id=99)),
        upload=AsyncMock(),
        errors=[],
        process_input=agents.agent_message_handler,
    )
    cm = SimpleNamespace(
        get_user_role=lambda _uid: state.role,
        register_pending=Mock(return_value=True),
    )
    monkeypatch.setattr(auth, "get_config_manager", lambda: cm)
    monkeypatch.setattr(auth, "_capture_identity", Mock())
    monkeypatch.setattr(auth, "_refresh_active_server", Mock())
    monkeypatch.setattr(auth, "_notify_admin_new_user", AsyncMock())
    monkeypatch.setattr(Message, "answer", state.reply)
    patch_agents(monkeypatch, "agent_message_handler", state.ai)
    monkeypatch.setattr(bots, "handle_config_file_upload", state.upload)
    state.app = offline_app(tmp_path)
    register_handlers()

    async def error_handler(update, context):
        state.errors.append(context.error)

    state.app.error_handler = error_handler
    await state.app.initialize()

    async def send(content=None, **kwargs):
        update = _update(state.app, content, **kwargs)
        await state.app.feed(update)
        assert not state.errors
        return update

    state.send = send
    state.cm = cm
    state.user_data = state.app.store.get(7)
    # Forms the tests arm directly were armed in the chat they send from.
    state.user_data[INPUT_CHAT_KEY] = 7
    yield state
    await state.app.close()


@pytest.fixture
def prompt_dispatch(dispatch, monkeypatch):
    patch_agents(monkeypatch, "agent_message_handler", dispatch.process_input)
    monkeypatch.setattr("config_manager.get_config_manager", lambda: dispatch.cm)
    patch_agents(
        monkeypatch,
        "_ensure_chat_session",
        AsyncMock(return_value=SimpleNamespace(conversation_id="test-conversation")),
    )
    dispatch.turn = AsyncMock(return_value=False)
    dispatch.secrets = AsyncMock()
    patch_agents(monkeypatch, "_stream_agent_turn", dispatch.turn)
    patch_agents(monkeypatch, "_handle_pasted_secrets", dispatch.secrets)
    return dispatch


def final_reply_text(dispatch):
    from condor.telegram.format import plain_text

    updates = [
        fields
        for method, fields in dispatch.app.bot.session.requests
        if method in {"sendMessage", "sendRichMessage", "editMessageText"}
    ]
    assert updates, "input errors settle the request's editable receipt"
    final = updates[-1]
    return (
        plain_text(json.loads(final["rich_message"])["html"])
        if "rich_message" in final
        else final["text"]
    )


@pytest.mark.asyncio
async def test_rich_message_dispatch_builds_prompt_request(prompt_dispatch):
    await prompt_dispatch.send(
        _rich(["Compare ", {"type": "bold", "text": "BTC"}, "\nwith ETH"])
    )
    prompt_dispatch.turn.assert_awaited_once()
    request = prompt_dispatch.turn.await_args.args[3]
    assert request.text == "Compare BTC\nwith ETH"
    assert request.request_id == "telegram:7:11"
    assert not request.images


@pytest.mark.asyncio
async def test_rich_secrets_are_scanned_before_prompting(prompt_dispatch):
    secret = (
        "legal winner thank year wave sausage worth useful legal winner thank yellow"
    )
    update = await prompt_dispatch.send(_rich({"type": "bold", "text": secret}))
    prompt_dispatch.secrets.assert_awaited_once()
    assert prompt_dispatch.secrets.await_args.args[2] == secret
    assert prompt_dispatch.secrets.await_args.kwargs["messages"] == (update.message,)


@pytest.mark.asyncio
@pytest.mark.parametrize("rich_is_quote", [False, True])
async def test_rejected_rich_content_does_not_skip_readable_secret_scan(
    prompt_dispatch, rich_is_quote
):
    secret = (
        "legal winner thank year wave sausage worth useful legal winner thank yellow"
    )
    rich = {"rich_message": {"blocks": [_rich_photo_block()]}}
    await prompt_dispatch.send(
        {
            **({"text": secret} if rich_is_quote else rich),
            "reply_to_message": {
                "message_id": 10,
                "date": 1,
                "chat": {"id": 7, "type": "private"},
                **(rich if rich_is_quote else {"text": secret}),
            },
        }
    )
    prompt_dispatch.secrets.assert_awaited_once()
    assert prompt_dispatch.secrets.await_args.args[2] == secret
    prompt_dispatch.turn.assert_not_awaited()
    prompt_dispatch.reply.assert_not_awaited()
    assert "not supported" in final_reply_text(prompt_dispatch)


@pytest.mark.asyncio
@pytest.mark.parametrize("external", [False, True])
async def test_rich_reply_dispatch_preserves_quoted_text(prompt_dispatch, external):
    content = {"text": "Explain"}
    if external:
        content["external_reply"] = {
            "origin": {"type": "hidden_user", "date": 1, "sender_user_name": "Tester"},
            **_rich("Original question"),
        }
    else:
        content["reply_to_message"] = {
            "message_id": 10,
            "date": 1,
            "chat": {"id": 7, "type": "private"},
            **_rich("Original question"),
        }
    await prompt_dispatch.send(content)
    assert (
        prompt_dispatch.turn.await_args.args[3].text
        == "Explain\n\nReplied-to message:\nOriginal question"
    )
    assert prompt_dispatch.secrets.await_args.args[2] == "Explain\n\nOriginal question"


@pytest.mark.asyncio
async def test_rich_reset_uses_own_text_even_when_reply_contains_media(
    prompt_dispatch, monkeypatch
):
    patch_agents(monkeypatch, "get_session", AsyncMock(return_value=object()))
    destroy = AsyncMock()
    patch_agents(monkeypatch, "destroy_session", destroy)
    content = _reply_image()
    del content["text"]
    content.update(_rich("-"))
    await prompt_dispatch.send(content)
    destroy.assert_awaited_once_with(7)
    prompt_dispatch.turn.assert_not_awaited()
    assert "Session reset" in prompt_dispatch.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_caption_dash_does_not_become_a_reset(prompt_dispatch, monkeypatch):
    destroy = AsyncMock()
    patch_agents(monkeypatch, "destroy_session", destroy)
    # A caption alone is enough to expose accidental control parsing without
    # involving a download in this dispatcher regression test.
    await prompt_dispatch.send({"caption": "-"})
    destroy.assert_not_awaited()
    assert prompt_dispatch.turn.await_args.args[3].text == "-"


@pytest.mark.asyncio
async def test_rich_armed_agent_input_receives_flattened_text(
    prompt_dispatch, monkeypatch
):
    compact = AsyncMock()
    patch_agents(monkeypatch, "_do_compact_from_message", compact)
    prompt_dispatch.user_data["agent_compact_custom"] = True
    await prompt_dispatch.send(_rich("Keep the decisions"))
    compact.assert_awaited_once()
    assert compact.await_args.args[2] == "Keep the decisions"
    prompt_dispatch.turn.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content, expected",
    [
        ({"rich_message": {"blocks": [_rich_photo_block()]}}, "not supported"),
        (_rich("x" * 32769), "32,768"),
    ],
)
async def test_invalid_rich_message_is_refused_before_prompting(
    prompt_dispatch, content, expected
):
    await prompt_dispatch.send(content)
    prompt_dispatch.turn.assert_not_awaited()
    prompt_dispatch.reply.assert_not_awaited()
    assert expected in final_reply_text(prompt_dispatch)


@pytest.mark.asyncio
async def test_obsolete_native_stop_is_not_treated_as_chat_input(dispatch, monkeypatch):
    incoming = update_from(
        dispatch.app.bot,
        {
            "update_id": 21,
            "stopped_message_generation": {
                "chat": {"id": 7, "type": "private"},
                "draft_id": 10,
            },
        },
    )
    await dispatch.app.feed(incoming)
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
    assert dispatch.ai.await_args.args[0].raw is update
    assert dispatch.user_data["_user_id"] == 7
    dispatch.reply.assert_not_called()
    assert len(dispatch.app.dispatcher.message.handlers) == 1


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
    patch_agents(monkeypatch, "stop_command", stop)
    register_handlers()
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
@pytest.mark.parametrize("rich", [False, True])
async def test_existing_forms_consume_plain_text(
    dispatch, monkeypatch, key, state, target, chat_type, rich
):
    handler = AsyncMock(return_value=True)
    monkeypatch.setattr(target, handler)
    dispatch.user_data[key] = state
    await dispatch.send(
        _rich("requested value") if rich else {"text": "requested value"},
        chat_type=chat_type,
    )
    handler.assert_awaited_once()
    assert handler.await_args.args[0].message.text == "requested value"
    dispatch.ai.assert_not_called()


@pytest.mark.asyncio
async def test_trading_form_priority_is_preserved(dispatch, monkeypatch):
    cex, dex = AsyncMock(), AsyncMock()
    monkeypatch.setattr("handlers.cex.cex_message_handler", cex)
    monkeypatch.setattr("handlers.dex.dex_message_handler", dex)
    dispatch.user_data.update(cex_state="trade_quick", dex_state="swap_quick")
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
        ("cex_state", "trade_quick"),
        ("dex_state", "swap_quick"),
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
    assert dispatch.upload.await_args.args[0].raw is update
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
        ("cex_state", "trade"),
        ("dex_state", "swap"),
        ("dex_state", "add_position"),
        ("dex_state", "pool_list_results"),
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
    assert processor.await_args.args[0].raw is update
    assert processor.await_args.args[2] == "spread=0.01"
    dispatch.ai.assert_not_called()


@pytest.mark.asyncio
async def test_actual_bot_form_consumes_rich_text_without_mutating_update(
    dispatch, monkeypatch
):
    processor = AsyncMock()
    monkeypatch.setitem(bots._TEXT_INPUT_HANDLERS, "cfg_bulk_edit", processor)
    dispatch.user_data["bots_state"] = "cfg_bulk_edit"
    update = await dispatch.send(_rich("  spread=0.01  "))
    processor.assert_awaited_once()
    form_update = processor.await_args.args[0]
    assert processor.await_args.args[2] == "spread=0.01"
    assert form_update.message.text == "  spread=0.01  "
    assert form_update.message.bot is dispatch.app.bot
    assert form_update.message.message_id == update.message.message_id
    assert update.message.text is None
    dispatch.ai.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("quoted", [False, True])
async def test_form_rejects_nested_rich_media_without_consuming_state(
    dispatch, monkeypatch, quoted
):
    processor = AsyncMock()
    monkeypatch.setitem(bots._TEXT_INPUT_HANDLERS, "cfg_bulk_edit", processor)
    dispatch.user_data["bots_state"] = "cfg_bulk_edit"
    content = {
        "rich_message": {
            "blocks": [
                {"type": "paragraph", "text": "spread=0.01"},
                {
                    "type": "details",
                    "summary": "Chart",
                    "blocks": [_rich_photo_block()],
                },
            ]
        }
    }
    if quoted:
        content = {
            **_rich("spread=0.01"),
            "reply_to_message": {
                "message_id": 10,
                "date": 1,
                "chat": {"id": 7, "type": "private"},
                **content,
            },
        }
    await dispatch.send(content)
    processor.assert_not_awaited()
    dispatch.ai.assert_not_called()
    assert dispatch.user_data["bots_state"] == "cfg_bulk_edit"
    assert "/cancel" in dispatch.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_form_reports_oversized_rich_text(dispatch, monkeypatch):
    handler = AsyncMock()
    monkeypatch.setattr("handlers.cex.cex_message_handler", handler)
    dispatch.user_data["cex_state"] = "trade_set_amount"
    await dispatch.send(_rich("x" * 32769))
    handler.assert_not_awaited()
    assert "32,768" in dispatch.reply.await_args.args[0]
    assert dispatch.user_data["cex_state"] == "trade_set_amount"


@pytest.mark.asyncio
async def test_progressive_bot_form_keeps_its_existing_update_contract(
    dispatch, monkeypatch
):
    processor = AsyncMock()
    monkeypatch.setattr(bots, "handle_deploy_progressive_input", processor)
    dispatch.user_data["bots_state"] = "deploy_progressive"
    update = await dispatch.send({"text": "20"})
    processor.assert_awaited_once()
    assert processor.await_args.args[0].raw is update
    assert len(processor.await_args.args) == 2
    dispatch.ai.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_type", ["private", "group"])
async def test_form_armed_in_another_chat_never_reads_this_one(
    dispatch, monkeypatch, chat_type
):
    handler = AsyncMock(return_value=True)
    monkeypatch.setattr("handlers.cex.cex_message_handler", handler)
    dispatch.user_data["cex_state"] = "trade_set_amount"
    dispatch.user_data[INPUT_CHAT_KEY] = 7
    await dispatch.send({"text": "btc buy 1"}, chat_type=chat_type, chat_id=-100)
    handler.assert_not_called()
    assert dispatch.ai.await_count == (1 if chat_type == "private" else 0)
    assert dispatch.user_data["cex_state"] == "trade_set_amount"


@pytest.mark.asyncio
async def test_arming_an_input_records_its_chat():
    async def arm(update, context):
        context.user_data["cex_state"] = "trade_set_amount"

    async def browse(update, context):
        pass

    user_data = {}
    context = SimpleNamespace(user_data=user_data)
    await track_input_chat(arm)(
        SimpleNamespace(effective_chat=SimpleNamespace(id=5)), context
    )
    assert user_data[INPUT_CHAT_KEY] == 5
    # A tap elsewhere that arms nothing new leaves the binding alone.
    await track_input_chat(browse)(
        SimpleNamespace(effective_chat=SimpleNamespace(id=9)), context
    )
    assert user_data[INPUT_CHAT_KEY] == 5


@pytest.mark.asyncio
async def test_unrouted_callback_is_answered(dispatch):
    await dispatch.app.feed(
        update_from(
            dispatch.app.bot,
            {
                "update_id": 21,
                "callback_query": {
                    "id": "q1",
                    "chat_instance": "c",
                    "data": "nothing:routes:this",
                    "from": {"id": 7, "is_bot": False, "first_name": "Tester"},
                    "message": {
                        "message_id": 3,
                        "date": 1,
                        "chat": {"id": 7, "type": "private"},
                    },
                },
            },
        )
    )
    methods = [method for method, _ in dispatch.app.bot.session.requests]
    assert "answerCallbackQuery" in methods


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "armed,expected",
    [(False, "Nothing to cancel"), (True, "Input cancelled")],
)
async def test_cancel_says_whether_anything_was_pending(dispatch, armed, expected):
    if armed:
        dispatch.user_data["cex_state"] = "trade_set_amount"
    await dispatch.send(
        {
            "text": "/cancel",
            "entities": [{"type": "bot_command", "offset": 0, "length": 7}],
        }
    )
    text = dispatch.reply.await_args.args[0]
    assert expected in text
    assert "cex_state" not in dispatch.user_data
