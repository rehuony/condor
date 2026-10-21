"""Telegram delivery for server-initiated turns and independent transcript notes.

Wake turns use the same editable reply as human input. The runtime supplies
request identity before opening the receipt. Stop cancels only that runtime
request; it never cancels the background producer awaiting the wake.

Register once at process start with :func:`register_wake` — not at import —
so a handler hot reload cannot attach a second sink.
"""

from __future__ import annotations

from condor.runtime import TELEGRAM
from condor.runtime.events import RuntimeEvent
from condor.runtime.keys import SessionKey
from condor.runtime.wake import register_note_sink, register_sink_factory
from condor.telegram.output.renderer import RichReplyStreamer
from condor.telegram.output.timing import ReplyTiming
from condor.telegram.transport import send_markdown

NOTE_MARKERS = {
    "routine": "⚙️",
    "delegation": "🤖",
    "resume": "↩️",
    "notification": "🔔",
}
DEFAULT_NOTE_MARKER = "ℹ️"


class TelegramWakeSink:
    """Streams a server-initiated turn into a Telegram chat."""

    def __init__(self, chat_id: int, user_id: int | None = None):
        self._chat_id = chat_id
        self._user_id = user_id if user_id is not None else chat_id
        self._streamer: RichReplyStreamer | None = None
        self._conversation_id = ""
        self._request_id = ""
        self._timing: ReplyTiming | None = None

    def bind_request(self, conversation_id: str, request_id: str) -> None:
        """Bind the runtime's identity before opening a visible wake receipt."""
        self._conversation_id = conversation_id
        self._request_id = request_id
        self._timing = ReplyTiming(request_id)
        self._timing.mark("received")

    def is_stopped(self) -> bool:
        return bool(self._streamer and self._streamer.target.stopped)

    async def open(self) -> None:
        """Prepare output without making acceptance depend on Telegram delivery."""
        from condor.agents.delegate import resolve_bot

        self._streamer = RichReplyStreamer(
            bot=resolve_bot(),
            chat_id=self._chat_id,
            user_id=self._user_id,
            conversation_id=self._conversation_id,
            request_id=self._request_id,
            timing=self._timing,
        )
        # Runtime cancellation targets this request, never the background task
        # awaiting it. The open-to-prompt gap is checked by resume_session.
        self._streamer.begin_preparation("Preparing an update…", lambda: None)

    async def on_event(self, event: RuntimeEvent) -> None:
        if self._streamer is not None:
            await self._streamer.process_event(event)

    async def close(self) -> None:
        if self._streamer is not None:
            try:
                await self._streamer.finalize()
            finally:
                await self._streamer.stop_edit_loop()


def _telegram_wake_sink(
    key: SessionKey, user_id: int | None
) -> TelegramWakeSink | None:
    chat_id = key.telegram_chat_id
    return TelegramWakeSink(chat_id, user_id) if chat_id is not None else None


def note_text(text: str, kind: str) -> str:
    """Keep the note's Markdown intact for the shared Telegram renderer."""
    marker = NOTE_MARKERS.get(kind, DEFAULT_NOTE_MARKER)
    return f"{marker} {text}"


async def _deliver_note(
    key: SessionKey, user_id: int | None, text: str, kind: str
) -> None:
    """Show an out-of-band transcript note in a Telegram chat (CORR-266)."""
    from condor.agents.delegate import resolve_bot

    chat_id = key.telegram_chat_id
    if chat_id is None:
        return

    bot = resolve_bot()
    body = note_text(text, kind)
    await send_markdown(bot, chat_id, body)


def register_wake() -> None:
    """Attach the Telegram wake and note sinks. Safe to call more than once."""
    register_sink_factory(TELEGRAM, _telegram_wake_sink)
    register_note_sink(TELEGRAM, _deliver_note)
