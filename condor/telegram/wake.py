"""Telegram delivery for server-initiated turns and independent transcript notes.

Wake turns use the same presenter as human input. Opening a sink performs no
Telegram I/O, so a failed progress message cannot prevent the recorded answer
from receiving a final delivery attempt.

Register once at process start with :func:`register_wake` — not at import —
so a handler hot reload cannot attach a second sink.
"""

from __future__ import annotations

from condor.runtime import TELEGRAM
from condor.runtime.events import RuntimeEvent
from condor.runtime.keys import SessionKey
from condor.runtime.wake import register_note_sink, register_sink_factory
from condor.telegram.output.renderer import RichReplyStreamer
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

    def __init__(self, chat_id: int):
        self._chat_id = chat_id
        self._streamer: RichReplyStreamer | None = None

    async def open(self) -> None:
        """Prepare output without making acceptance depend on Telegram delivery."""
        from condor.agents.delegate import resolve_bot

        self._streamer = RichReplyStreamer(
            bot=resolve_bot(), chat_id=self._chat_id, user_id=self._chat_id
        )
        self._streamer.start_edit_loop()

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
    return TelegramWakeSink(chat_id) if chat_id is not None else None


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
