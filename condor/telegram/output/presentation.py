"""Stable source segments and safe labels for Telegram message updates."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from condor.runtime.danger import tool_call_input, tool_call_name
from condor.telegram.format import MAX_MESSAGE_LEN, render_rich_reply, split_text

BODY_MAX_LEN = 3500

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_TOOL_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,79}\Z", re.ASCII)
_TOOL_ACTIONS = {
    "read": ("Reading", "read"),
    "edit": ("Editing", "edit"),
    "write": ("Writing", "write"),
    "delete": ("Deleting", "delete"),
    "move": ("Moving", "move"),
    "search": ("Searching", "search"),
    "execute": ("Running", "run"),
    "fetch": ("Fetching", "fetch"),
    "check": ("Checking", "check"),
}
_NAMED_KINDS = {
    "Read": "read",
    "read_file": "read",
    "read_text_file": "read",
    "Edit": "edit",
    "MultiEdit": "edit",
    "edit_file": "edit",
    "Write": "write",
    "write_file": "write",
    "Glob": "search",
    "Grep": "search",
    "ToolSearch": "search",
    "WebSearch": "search",
    "Bash": "execute",
    "WebFetch": "fetch",
    "web_fetch": "fetch",
}
_TARGET_FIELDS = frozenset(
    {
        "path",
        "file_path",
        "file",
        "url",
        "trading_pair",
        "trading_pairs",
        "connector_name",
        "name",
        "action",
    }
)
_SERVICE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,47}\Z", re.ASCII)
_TRADING_PAIR = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}\Z", re.ASCII)


def _utf16_size(text: str) -> int:
    return sum(2 if ord(char) > 0xFFFF else 1 for char in text)


def _prefix(text: str, limit: int) -> str:
    size = 0
    for index, char in enumerate(text):
        size += 2 if ord(char) > 0xFFFF else 1
        if size > limit:
            return text[:index]
    return text


@dataclass(frozen=True)
class Segment:
    """A contiguous raw-body range; offsets are Python character indices."""

    start: int
    end: int
    text: str
    sealed: bool = False


class SegmentBuffer:
    """Append text without moving boundaries of already displayed segments."""

    def __init__(self) -> None:
        self.body = ""
        self.segments: list[Segment] = []
        self._finalized = False

    def append(self, text: str) -> None:
        if self._finalized:
            raise ValueError("Cannot append after finalizing the message")
        if not text:
            return
        self.body += text
        active = self.segments.pop() if self.segments else None
        start = active.start if active else 0
        chunks = split_text(self.body[start:], BODY_MAX_LEN)

        # A newly arrived paragraph boundary must not retract text the user
        # already saw. Fill the remaining space before sealing that segment.
        if active and start + len(chunks[0]) < active.end:
            available = BODY_MAX_LEN - _utf16_size(active.text)
            tail = self.body[active.end :]
            addition = (
                split_text(tail, available)[0]
                if available >= 2
                else _prefix(tail, available)
            )
            first = active.text + addition
            chunks = [first, *split_text(self.body[start + len(first) :], BODY_MAX_LEN)]

        for index, chunk in enumerate(chunks):
            end = start + len(chunk)
            self.segments.append(Segment(start, end, chunk, index < len(chunks) - 1))
            start = end

    def finalize(self) -> None:
        """Seal the last segment without repartitioning the answer."""
        self._finalized = True
        if self.segments:
            self.segments[-1] = replace(self.segments[-1], sealed=True)


def render_preview(body: str, status: str) -> str:
    """Append a bounded status to the active segment, outside the raw body."""
    status = status.strip() or "Preparing response…"
    if not body:
        return _prefix(status, MAX_MESSAGE_LEN)
    remaining = MAX_MESSAGE_LEN - _utf16_size(body) - 2
    if remaining <= 0:
        return body
    return body + "\n\n" + _prefix(status, remaining)


def _protected_blocks(text: str) -> list[tuple[int, int, bool]]:
    """Find fences and pipe-row runs; uncertain structure stays literal."""
    blocks: list[tuple[int, int, bool]] = []
    fence: tuple[int, str] | None = None
    table_start: int | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        match = _FENCE.match(content)
        if fence:
            if (
                match
                and match[1][0] == fence[1][0]
                and len(match[1]) >= len(fence[1])
                and not match[2].strip()
            ):
                blocks.append((fence[0], offset + len(line), True))
                fence = None
        elif match:
            if table_start is not None:
                blocks.append((table_start, offset, True))
                table_start = None
            fence = (offset, match[1])
        elif "|" in content:
            if table_start is None:
                table_start = offset
        elif table_start is not None:
            blocks.append((table_start, offset, True))
            table_start = None
        offset += len(line)
    if fence:
        blocks.append((fence[0], len(text), False))
    if table_start is not None:
        blocks.append((table_start, len(text), True))
    return blocks


def _paragraph_boundary(text: str, offset: int) -> bool:
    return offset in (0, len(text)) or text[:offset].endswith(("\n\n", "\r\n\r\n"))


def final_payload(segment: Segment, full_text: str) -> tuple[str, str | None]:
    """Return source text plus compiled HTML at complete block boundaries.

    Source remains available for an exact fallback. The formatter never changes
    segment offsets or creates new messages; unsafe boundaries stay literal.
    """
    for start, end, complete in _protected_blocks(full_text):
        if segment.start < end and segment.end > start:
            if not complete or segment.start > start or segment.end < end:
                return segment.text, None
    if not _paragraph_boundary(full_text, segment.start) or not _paragraph_boundary(
        full_text, segment.end
    ):
        return segment.text, None
    return segment.text, render_rich_reply(segment.text)


def tool_inputs(value: object) -> dict:
    """Keep only fields used by the status projection; empty patches erase nothing."""
    arguments = tool_call_input({"input": value}) or {}
    return {
        key: item
        for key, item in arguments.items()
        if key in _TARGET_FIELDS and isinstance(item, (str, list)) and item
    }


def _single_line(value: object, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        return ""
    if any(
        unicodedata.category(char).startswith("C") or char in "\u2028\u2029"
        for char in value
    ):
        return ""
    return value.strip()


def _basename(value: object) -> str:
    path = _single_line(value, 4096)
    if not path or "://" in path:
        return ""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if name in {".", ".."} or any(char in name for char in ':?*<>|"'):
        return ""
    name = name.strip()
    return name if _utf16_size(name) <= 64 else _prefix(name, 63) + "…"


def _hostname(value: object) -> str:
    url = _single_line(value, 4096)
    if not url or any(char.isspace() for char in url):
        return ""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if parsed.scheme in {"http", "https"} and _SERVICE_NAME.fullmatch(host):
            return host
    except ValueError:
        pass
    return ""


def tool_action(
    title: str = "",
    kind: str = "",
    identifier: str = "",
    *,
    arguments: dict | None = None,
    status: str = "in_progress",
) -> str:
    """Project a known action and short target, never arbitrary titles or arguments."""
    name = ""
    for value in (identifier, title):
        if isinstance(value, str) and _TOOL_IDENTIFIER.fullmatch(value):
            name = tool_call_name({"tool": value})
            break
    arguments = arguments or {}
    action_name = arguments.get("action", "")
    action_name = action_name if isinstance(action_name, str) else None
    kind = kind if isinstance(kind, str) else ""
    kind = _NAMED_KINDS.get(name, kind)
    target = ""
    if name in {"get_prices", "get_market_data"}:
        kind = "fetch"
        pair = arguments.get("trading_pair")
        if name == "get_prices":
            pairs = arguments.get("trading_pairs")
            pair = pairs[0] if isinstance(pairs, list) and len(pairs) == 1 else None
        pair = pair.upper() if isinstance(pair, str) else ""
        pair = pair if _TRADING_PAIR.fullmatch(pair) else ""
        connector = arguments.get("connector_name")
        connector = (
            connector
            if isinstance(connector, str) and _SERVICE_NAME.fullmatch(connector)
            else ""
        )
        item = "prices" if name == "get_prices" else "market data"
        if name == "get_market_data" and action_name in {
            "candles",
            "historical_candles",
        }:
            item = "candles"
        target = (
            f"{pair} {item}" if pair else f"{connector} {item}" if connector else item
        )
    elif name == "manage_servers":
        kind = "check" if action_name in {"list", "status"} else ""
        if kind:
            service = arguments.get("name")
            target = (
                service
                if isinstance(service, str) and _SERVICE_NAME.fullmatch(service)
                else "Hummingbot API"
            )
    elif name == "explore_geckoterminal":
        kind, target = "fetch", "GeckoTerminal"
    elif name == "run_code":
        if action_name in {"history", "get"}:
            kind, target = "read", "code history"
        elif action_name in {"", "run"}:
            kind, target = "execute", "code"
        else:
            kind = ""
    elif name == "manage_skill" and action_name in {"read_file", "write_file"}:
        kind = "read" if action_name == "read_file" else "write"

    if not target and kind in {"read", "edit", "write", "delete", "move", "search"}:
        target = next(
            (
                value
                for field in ("file_path", "path", "file")
                if (value := _basename(arguments.get(field)))
            ),
            "",
        )
    if not target and kind == "fetch":
        target = _hostname(arguments.get("url"))
    action = _TOOL_ACTIONS.get(kind)
    if action is None:
        return "Preparing tool…" if status == "pending" else "Using tool…"
    if status == "pending":
        return (
            f"Preparing to {action[1]}{(' ' + target) if target else ''}".rstrip("…")
            + "…"
        )
    return f"{action[0]} {target}" if target else action[0] + "…"
