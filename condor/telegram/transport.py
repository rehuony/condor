"""Send rendered text to Telegram completely, honouring rate limits."""

from __future__ import annotations

import asyncio
import io
import os
from typing import Any

from aiogram.types import BufferedInputFile

from .errors import BadRequest, RateLimited, TelegramFailure, retry_delay
from .format import (
    RICH_MAX_LEN,
    TelegramText,
    plain_text,
    render_html,
    render_markdown,
    split_text,
)

_standalone: Any = None


def standalone_bot(token: str | None = None):
    """A bot client for code with no running application behind it.

    Called with a token (an MCP subprocess that must push a message itself) it
    returns a fresh ``Bot`` to use as an ``async with`` context manager. Called
    without one it returns the process-wide client built from
    ``TELEGRAM_TOKEN`` — the same object every time, closed by
    :func:`close_standalone_bot` at shutdown — or ``None`` when no token is
    configured, so the caller can fall through to the dashboard bell.
    """
    from .app import make_bot

    if token:
        return make_bot(token)
    global _standalone
    if _standalone is None:
        configured = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get(
            "TELEGRAM_TOKEN", ""
        )
        if not configured:
            return None
        _standalone = make_bot(configured)
    return _standalone


async def close_standalone_bot() -> None:
    global _standalone
    bot, _standalone = _standalone, None
    if bot is not None:
        await bot.session.close()


def as_input_file(obj: Any, filename: str | None = None) -> Any:
    """Wrap in-memory bytes for upload; pass anything aiogram already accepts.

    Charts are rendered into ``BytesIO`` buffers; aiogram uploads ``InputFile``
    objects only, so the buffer's remaining bytes become a
    :class:`BufferedInputFile` named after ``filename`` or the buffer's
    ``name``. Strings (file ids, URLs) and ``InputFile`` instances are returned
    as they are.
    """
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return BufferedInputFile(bytes(obj), filename=filename or "file")
    if isinstance(obj, io.IOBase):
        name = filename or getattr(obj, "name", None)
        name = (
            os.path.basename(os.fsdecode(name))
            if isinstance(name, (str, os.PathLike))
            else "file"
        )
        return BufferedInputFile(obj.read(), filename=name)
    return obj


def message_id_of(result) -> int | None:
    """The message id a send or edit came back with, if it produced one.

    A send returns a ``Message``; an edit of a message the bot did not send
    returns ``True``, and a notification filed to the bell returns its record.
    Only the first has an id.
    """
    value = getattr(result, "message_id", None)
    return value if type(value) is int and value > 0 else None


def reached_telegram(result) -> bool:
    """Did a send actually reach Telegram?

    The bot raises on failure and returns a ``Message`` on success; ``None``
    means no sender was available. Callers that can receive other objects (a
    notification filed to the bell) rule those out before asking.
    """
    return result is not None


async def telegram_call(method, *, retry_rate_limits: bool = True, **kwargs):
    """Call a bot method, waiting out Telegram's explicit rate limit."""
    for attempt in range(3):
        try:
            return await method(**kwargs)
        except RateLimited as exc:
            if not retry_rate_limits or attempt == 2:
                raise
            await asyncio.sleep(retry_delay(exc))


class _RichUnavailable(Exception):
    """The first rich page was rejected as unparseable; fall back to HTML."""


def _is_parse_error(exc: BadRequest) -> bool:
    text = str(exc).lower()
    return "parse" in text or "entit" in text


def _first_markup(reply_markup, results: list) -> dict:
    if reply_markup is not None and not results:
        return {"reply_markup": reply_markup}
    return {}


def _require_complete(results: list) -> None:
    if not results or any(result is None for result in results):
        raise TelegramFailure(
            None, "Telegram did not acknowledge the complete notification"
        )


async def send_markdown(bot, chat_id: int, text: str, *, reply_markup=None) -> list:
    """Send every page in order; never claim success for a partial delivery."""
    return await send_text(
        bot, chat_id, text, parse_mode="Markdown", reply_markup=reply_markup
    )


async def send_text(
    bot, chat_id: int, text: str, *, parse_mode: str = "", reply_markup=None
) -> list:
    """Deliver Markdown as a rich message, Telegram HTML/MarkdownV2, or literal text.

    Standard Markdown prefers ``send_rich_message`` (native tables, 32000-character
    pages). A parse rejection or a sender without that method falls through to
    the HTML renderer.
    """
    from .types import NO_LINK_PREVIEW

    results: list = []
    if parse_mode == "Markdown" and callable(getattr(bot, "send_rich_message", None)):
        try:
            await _deliver_rich(bot, chat_id, text, results, reply_markup=reply_markup)
            return results
        except _RichUnavailable:
            results.clear()
    await _deliver_pages(
        bot,
        chat_id,
        text,
        results,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        link_preview=NO_LINK_PREVIEW,
    )
    return results


async def _deliver_rich(bot, chat_id: int, text: str, results: list, *, reply_markup):
    from aiogram.types import InputRichMessage

    for chunk in split_text(text, RICH_MAX_LEN) or [text]:
        extra = _first_markup(reply_markup, results)
        try:
            result = await telegram_call(
                bot.send_rich_message,
                chat_id=chat_id,
                rich_message=InputRichMessage(markdown=chunk),
                **extra,
            )
        except BadRequest as exc:
            if not results and _is_parse_error(exc):
                raise _RichUnavailable() from exc
            raise
        results.append(result)
    _require_complete(results)


async def _deliver_pages(
    bot,
    chat_id: int,
    text: str,
    results: list,
    *,
    parse_mode: str,
    reply_markup,
    link_preview,
) -> None:
    wire_mode = "HTML"
    if parse_mode == "Markdown":
        pages = render_markdown(text)
    elif parse_mode == "HTML":
        pages = render_html(text)
    elif parse_mode in {"", "MarkdownV2"}:
        # MarkdownV2 is already Telegram markup. Keep its escaping intact;
        # a page split through an entity can fall back to the literal source.
        pages = [TelegramText(chunk, "") for chunk in split_text(text)]
        wire_mode = parse_mode
    else:
        raise ValueError(f"Unsupported notification parse mode: {parse_mode}")
    for page in pages:
        extra = _first_markup(reply_markup, results)
        try:
            result = await telegram_call(
                bot.send_message,
                chat_id=chat_id,
                text=page.html if wire_mode == "HTML" else page.text,
                **({"parse_mode": wire_mode} if wire_mode else {}),
                link_preview_options=link_preview,
                **extra,
            )
            results.append(result)
        except BadRequest as exc:
            if not wire_mode or not _is_parse_error(exc):
                raise
            fallback = plain_text(page.html) if wire_mode == "HTML" else page.text
            for chunk in split_text(fallback):
                extra = _first_markup(reply_markup, results)
                results.append(
                    await telegram_call(
                        bot.send_message,
                        chat_id=chat_id,
                        text=chunk,
                        link_preview_options=link_preview,
                        **extra,
                    )
                )
    _require_complete(results)
