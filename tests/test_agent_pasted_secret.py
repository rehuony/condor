"""What Telegram does about a pasted key (FEAT-056).

The safety property is not tested here — it lives on the funnel and is pinned
in ``tests/runtime/test_secrets_funnel.py``. What this file pins is the half
only this surface can do: deleting the message that carried the phrase, saying
why the text on screen just changed, and warning about the ambiguous shapes
rarely enough that the warning is still read.
"""

import asyncio
from types import SimpleNamespace

import pytest

from handlers.agents import _SECRET_SEEN_KEY, _handle_pasted_secrets
from tests.conftest import patch_agents

SEED = "legal winner thank year wave sausage worth useful legal winner thank yellow"
TX_HASH = "0x" + "9f" * 32


def _update():
    """A private-chat message that records whether it was deleted."""
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=4242, type="private"),
        effective_user=SimpleNamespace(id=7),
        deleted=[],
    )

    async def delete():
        update.deleted.append(True)

    update.message = SimpleNamespace(delete=delete)
    return update


def _context(**user_data):
    """A bot that records what it sent, plus empty user/chat data."""
    sent: list = []

    async def send_message(chat_id, text, **kwargs):
        sent.append(SimpleNamespace(text=text, markup=kwargs.get("reply_markup")))
        return SimpleNamespace(message_id=100 + len(sent))

    async def send_rich_message(**kwargs):
        text = getattr(kwargs.get("rich_message"), "markdown", None) or kwargs.get(
            "text", ""
        )
        sent.append(SimpleNamespace(text=text, markup=kwargs.get("reply_markup")))
        return SimpleNamespace(message_id=100 + len(sent))

    async def send_rich_message_draft(**kwargs):
        return True

    return SimpleNamespace(
        bot=SimpleNamespace(
            send_message=send_message,
            send_rich_message=send_rich_message,
            send_rich_message_draft=send_rich_message_draft,
        ),
        user_data=dict(user_data),
        chat_data={},
        sent=sent,
    )


def _run(text, context=None, update=None):
    update = update or _update()
    context = context or _context()
    asyncio.run(_handle_pasted_secrets(update, context, text))
    return SimpleNamespace(
        deleted=update.deleted, sent=context.sent, context=context, update=update
    )


def test_a_message_carrying_a_phrase_is_deleted_and_explained():
    out = _run(f"import this for me: {SEED}")
    assert out.deleted == [True]
    assert len(out.sent) == 1
    assert "/gateway" in out.sent[0].text
    assert "sausage" not in out.sent[0].text


def test_a_delete_that_fails_still_explains():
    """Telegram keeps the message until the delete lands, and it is best
    effort — the same bare-except the wallet flow has carried for years."""
    update = _update()

    async def boom():
        raise RuntimeError("message can't be deleted")

    update.message.delete = boom
    out = _run(SEED, update=update)
    assert len(out.sent) == 1
    assert "/gateway" in out.sent[0].text


def test_ordinary_text_says_nothing_and_deletes_nothing():
    for text in ("how is SOL-USDC doing?", "restart hummingbot-pmm-1", ""):
        out = _run(text)
        assert out.deleted == []
        assert out.sent == []


def test_a_transaction_hash_is_warned_about_once_per_conversation():
    context = _context()
    sent = context.sent
    _run(f"did {TX_HASH} land?", context=context)
    assert len(sent) == 1
    assert "transaction hash" in sent[0].text
    assert sent[0].markup is not None  # the "stop warning me" button

    _run(f"and {TX_HASH} again?", context=context)
    assert len(sent) == 1, "a warning that fires every time is one nobody reads"


def test_each_kind_gets_its_own_warning():
    """Being warned about a hash must not use up the warning about a key."""
    sol_sig = (
        "5wHu1qwD4kLwYqLNGjaKfHUDNCLLDFFPGz1cUKb1t8HBxXpJhVFq"
        "1PbwzTV1RxRuFuvLWqJwHtDsL1s9jUn9Xg1H"
    )
    context = _context()
    sent = context.sent
    _run(f"check {TX_HASH}", context=context)
    _run(f"check {sol_sig}", context=context)
    assert len(sent) == 2
    assert "base58" in sent[1].text


def test_a_new_conversation_is_warned_again():
    context = _context()
    sent = context.sent
    _run(f"did {TX_HASH} land?", context=context)
    # A reset leaves the chat on another conversation id.
    from condor.preferences import set_chat_binding

    set_chat_binding(context.user_data, {"conversation_id": "conv-2"})
    _run(f"did {TX_HASH} land?", context=context)
    assert len(sent) == 2


def test_the_notice_obeys_the_silence_preference():
    context = _context()
    sent = context.sent
    from condor.preferences import set_secret_notices

    set_secret_notices(context.user_data, False)
    _run(f"did {TX_HASH} land?", context=context)
    assert sent == []


def test_silencing_the_notice_does_not_silence_the_removal():
    """Nothing about the certain shapes is optional. The message that explains
    a deletion is not a notice — it is why the user's text just vanished."""
    context = _context()
    sent = context.sent
    from condor.preferences import set_secret_notices

    set_secret_notices(context.user_data, False)
    out = _run(SEED, context=context)
    assert out.deleted == [True]
    assert len(sent) == 1


def test_the_seen_flag_is_a_kind_and_never_a_value():
    context = _context()
    _run(f"did {TX_HASH} land?", context=context)
    flags = context.chat_data[_SECRET_SEEN_KEY]
    assert flags == {"": ["evm-hex64"]}
    assert TX_HASH not in repr(flags)


@pytest.mark.parametrize("start_on", [True, False])
def test_the_settings_toggle_flips_the_preference(start_on):
    from condor.preferences import secret_notices_enabled, set_secret_notices

    user_data: dict = {}
    set_secret_notices(user_data, start_on)
    assert secret_notices_enabled(user_data) is start_on
    set_secret_notices(user_data, not start_on)
    assert secret_notices_enabled(user_data) is (not start_on)


# ── A spoken phrase (SEC-281) ────────────────────────────────────────────
#
# The unified handler renders sanitized transcription in the streamer's prefix.
# The raw transcript must still reach detection and the runtime's ingress funnel.


def _voice_update():
    """A private voice message that records its replies and its deletion."""
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=4242, type="private"),
        effective_user=SimpleNamespace(id=7),
        deleted=[],
        replies=[],
    )

    async def edit_text(text, **kwargs):
        update.replies.append(text)

    status_msg = SimpleNamespace(message_id=99, edit_text=edit_text)

    async def answer(text, **kwargs):
        update.replies.append(text)
        return status_msg

    async def delete():
        update.deleted.append(True)

    update.message = SimpleNamespace(
        message_id=10,
        chat=SimpleNamespace(id=4242),
        text=None,
        caption=None,
        photo=(),
        sticker=None,
        audio=None,
        document=None,
        media_group_id=None,
        reply_to_message=None,
        external_reply=None,
        voice=SimpleNamespace(file_id="voice", file_unique_id="voice", file_size=3),
        answer=answer,
        delete=delete,
    )
    return update


def _approved(monkeypatch):
    import config_manager
    from config_manager import UserRole

    monkeypatch.setattr(
        config_manager,
        "get_config_manager",
        lambda: SimpleNamespace(get_user_role=lambda user_id: UserRole.USER),
    )


def _run_streamed_answer(monkeypatch, spoken, answer="All set."):
    """Drive actual intake and streaming, replacing attachment I/O and the model."""
    import condor.telegram.input as telegram_input
    import handlers.agents as agents
    from condor.runtime.events import EventType, RuntimeEvent

    _approved(monkeypatch)

    async def get_session(chat_id):
        return SimpleNamespace(alive=True)

    patch_agents(monkeypatch, "get_session", get_session)

    async def resolve(incoming, **kwargs):
        assert len(incoming.media) == 1
        assert incoming.media[0].kind == "audio"
        return telegram_input.ResolvedInput(spoken, (), (spoken,))

    monkeypatch.setattr(
        telegram_input, "get_resolver", lambda bot: SimpleNamespace(resolve=resolve)
    )
    requests = []
    progress_seen = asyncio.Event()

    async def prompt(key, request, **kwargs):
        requests.append(request)
        yield RuntimeEvent(type=EventType.ACCEPTED)
        yield RuntimeEvent(type=EventType.TEXT, data={"text": answer})
        # Pin both silent progress and the permanent final-message surfaces.
        await asyncio.wait_for(progress_seen.wait(), timeout=1)
        yield RuntimeEvent.done("end_turn")

    monkeypatch.setattr(agents.runtime, "prompt", prompt)

    update = _voice_update()
    context = _context()
    edited = []
    drafts = []
    deleted_statuses = []

    async def send_rich_message_draft(**kwargs):
        drafts.append(getattr(kwargs.get("rich_message"), "markdown", ""))
        progress_seen.set()
        return True

    async def delete_message(**kwargs):
        deleted_statuses.append(kwargs["message_id"])
        return True

    context.bot.send_rich_message_draft = send_rich_message_draft
    context.bot.delete_message = delete_message
    asyncio.run(agents.agent_message_handler(update, context))
    return SimpleNamespace(
        edited=edited,
        drafts=drafts,
        deleted_statuses=deleted_statuses,
        context=context,
        requests=requests,
        update=update,
    )


def test_a_spoken_phrase_is_redacted_before_it_is_shown(monkeypatch):
    out = _run_streamed_answer(monkeypatch, SEED)
    assert out.drafts, "progress uses a native rich-message draft"
    assert "[redacted: mnemonic]" in out.context.sent[-1].text
    displayed = (
        out.drafts
        + out.edited
        + out.update.replies
        + [message.text for message in out.context.sent]
    )
    assert not any("sausage" in text for text in displayed)
    assert out.deleted_statuses == [99]
    assert "_voice_transcription" not in out.context.chat_data
    assert "_voice_placeholder" not in out.context.chat_data


def test_the_raw_transcript_still_reaches_detection_and_the_funnel(monkeypatch):
    out = _run_streamed_answer(monkeypatch, SEED)
    assert [request.text for request in out.requests] == [SEED]
    # Detection sees the original phrase and deletes the message carrying it.
    # The runtime funnel owns redaction before forwarding text to the model.
    assert out.update.deleted == [True]
    assert out.context.sent and "/gateway" in out.context.sent[0].text


def test_an_ordinary_transcription_stays_at_the_head_of_the_answer(monkeypatch):
    out = _run_streamed_answer(monkeypatch, "how is SOL-USDC doing?")
    for text in (out.context.sent[-1].text,):
        assert text.startswith("🎙 how is SOL-USDC doing?")
        assert "All set." in text
        assert "redacted" not in text
    assert out.drafts[-1] == "All set."
    assert out.deleted_statuses == [99]
    assert not out.update.deleted
