"""Telegram wake/note sinks live in ``condor.telegram.wake``.

This module keeps the import path tests already use.
"""

from condor.telegram.wake import (
    DEFAULT_NOTE_MARKER,
    NOTE_MARKERS,
    TelegramWakeSink,
    _deliver_note,
    _telegram_wake_sink,
    note_text,
    register_wake,
)

__all__ = [
    "DEFAULT_NOTE_MARKER",
    "NOTE_MARKERS",
    "TelegramWakeSink",
    "_deliver_note",
    "_telegram_wake_sink",
    "note_text",
    "register_wake",
]
