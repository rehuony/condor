"""Canonical ``surface:owner[:slot]`` session keys shared by every frontend."""

from __future__ import annotations

from dataclasses import dataclass

# Known surfaces. A surface identifies which frontend owns the session.
TELEGRAM = "tg"
WEB = "web"
MCP = "mcp"

SEPARATOR = ":"


@dataclass(frozen=True)
class SessionKey:
    """Immutable, hashable identifier for one chat session.

    ``owner`` is the Telegram ``chat_id`` for the ``tg`` surface and the Condor
    ``user_id`` for ``web``/``mcp``. It is always stored as a string so the key
    has one canonical text form.
    """

    surface: str
    owner: str
    slot: str = ""

    def __str__(self) -> str:
        if self.slot:
            return f"{self.surface}{SEPARATOR}{self.owner}{SEPARATOR}{self.slot}"
        return f"{self.surface}{SEPARATOR}{self.owner}"

    @classmethod
    def parse(cls, raw: str) -> "SessionKey":
        """Parse the canonical ``surface:owner[:slot]`` form."""
        parts = str(raw).split(SEPARATOR, 2)
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise ValueError(f"Not a canonical session key: {raw!r}")
        surface, owner = parts[0], parts[1]
        slot = parts[2] if len(parts) == 3 else ""
        return cls(surface=surface, owner=owner, slot=slot)

    @classmethod
    def telegram(cls, chat_id: int | str) -> "SessionKey":
        return cls(surface=TELEGRAM, owner=str(chat_id))

    @classmethod
    def web(cls, user_id: int | str, slot_id: str) -> "SessionKey":
        return cls(surface=WEB, owner=str(user_id), slot=str(slot_id))

    @classmethod
    def mcp(cls, user_id: int | str) -> "SessionKey":
        return cls(surface=MCP, owner=str(user_id))

    @property
    def is_telegram(self) -> bool:
        return self.surface == TELEGRAM

    @property
    def telegram_chat_id(self) -> int | None:
        """The Telegram chat id, or None when this is not a Telegram session."""
        if not self.is_telegram:
            return None
        try:
            return int(self.owner)
        except ValueError:
            return None


def slot_of(session_key: str) -> str:
    """The slot a session key belongs to, or "" if the key is not canonical.

    Non-web confirmations have no slot and remain answerable from the dashboard.
    An unreadable key also returns an empty slot instead of dropping the request.
    """
    try:
        return SessionKey.parse(session_key).slot
    except ValueError:
        return ""
