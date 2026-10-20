"""Telegram delivery for server-initiated turns and independent transcript notes.

Wake turns use the same presenter and acknowledgement protocol as human input.
Opening a sink performs no Telegram I/O, so a failed progress message cannot
prevent the recorded answer from receiving a final delivery attempt.
"""

from __future__ import annotations

from condor.runtime import TELEGRAM
from condor.runtime.events import RuntimeEvent
from condor.runtime.keys import SessionKey
from condor.runtime.wake import register_note_sink, register_sink_factory
from condor.telegram_text import send_markdown

from .stream import TelegramStreamer

# What a note is *about*, in one glyph. The web client separates a note from
# the agent's own words with an inset border; Telegram has no such affordance,
# so the marker is what stops a routine's outcome from reading as the agent
# saying it. Kinds are the ones ``ChatMessage.tsx`` renders as notes.
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
        self._streamer: TelegramStreamer | None = None

    async def open(self) -> None:
        """Prepare output without making acceptance depend on Telegram delivery."""
        from condor.agents.delegate import resolve_bot

        self._streamer = TelegramStreamer(
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
    """Show an out-of-band transcript note in a Telegram chat (CORR-266).

    The Telegram half of the channel whose web half is ``chat_ws._deliver_note``.
    Without it ``wake.deliver_note`` ran its whole gauntlet for a ``tg:`` key and
    then found no sink to hand the note to, so every note addressed at Telegram
    was dropped without a log line — a routine that finished while the user was
    looking at the chat said nothing.

    Sent as its own message rather than streamed: a note is already-recorded
    text, not a turn, so there is nothing to edit into place.

    A chat this process cannot reach leaves this inert rather than failing the
    caller, the same contract :meth:`TelegramWakeSink.open` states: the note is
    in the transcript either way, and ``wake._guard`` swallows what does raise.
    """
    from condor.agents.delegate import resolve_bot

    chat_id = key.telegram_chat_id
    if chat_id is None:
        return

    bot = resolve_bot()
    body = note_text(text, kind)
    await send_markdown(bot, chat_id, body)


# Both halves of the channel, registered here rather than imported by the
# runtime: ``condor.runtime`` must not depend on handler code (see
# ``client._local()``). The web surface registers the same pair in ``chat_ws``.
register_sink_factory(TELEGRAM, _telegram_wake_sink)
register_note_sink(TELEGRAM, _deliver_note)
