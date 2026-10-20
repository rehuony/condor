"""Normalize Telegram messages before doing bounded attachment I/O.

Extraction is pure and refuses an unsupported or oversized turn before fetching
anything. Resolution is all-or-nothing: a failed picture must never turn a
question about that picture into an apparently successful text-only prompt.
The application owns one resolver and closes its pooled HTTP session at shutdown.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit

import aiohttp
from PIL import Image
from telegram import ExternalReplyInfo, Message

from condor.runtime import attachments
from condor.runtime.models import PromptImage

MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_AUDIO_SECONDS = 600
MAX_DOCUMENT_BYTES = 128 * 1024
MAX_TEXT_CHARS = 32768
MAX_FILES_PER_TURN = attachments.MAX_PER_TURN
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
DOWNLOAD_TIMEOUT_SECONDS = 45
DOWNLOAD_CONCURRENCY = 4
IMAGE_VALIDATION_CONCURRENCY = 2
_CHUNK_BYTES = 64 * 1024
_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml", ".log"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_AUDIO_SUFFIXES = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".flac", ".aac"}
_UNSUPPORTED = (
    "animation",
    "video",
    "video_note",
    "contact",
    "location",
    "venue",
    "poll",
    "dice",
    "game",
    "paid_media",
    "story",
    "checklist",
    "invoice",
    "giveaway",
    "giveaway_winners",
    "passport_data",
    "successful_payment",
)
_MEDIA_FIELDS = ("photo", "sticker", "voice", "audio", "document", *_UNSUPPORTED)
_SUPPORTED_MESSAGE = (
    "Send text, a photo, a static PNG/JPEG/GIF/WebP image, a voice/audio message, "
    "or a UTF-8 text document (.txt, .md, .csv, .tsv, .json, .yaml, .yml, .log). "
    "Videos, animations, PDFs and other binary files are not supported."
)


class InputError(Exception):
    """A safe, user-facing refusal; never includes a Telegram file URL or token."""

    def __init__(self, code: str, user_message: str):
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message


@dataclass(frozen=True)
class MediaRef:
    kind: Literal["image", "audio", "text"]
    file_id: str
    unique_id: str
    size: int | None
    name: str = ""
    duration: float | None = None


@dataclass(frozen=True)
class TelegramInput:
    text: str
    media: tuple[MediaRef, ...]
    # Only the actual inbound messages, not the quoted messages.
    source_message_ids: tuple[int, ...]


@dataclass(frozen=True)
class ResolvedInput:
    text: str
    images: tuple[PromptImage, ...]
    transcriptions: tuple[str, ...]


def input_has_attachments(message: Message) -> bool:
    """Detect direct or immediately referenced media without reading any files.

    Form routers use this before consuming text. Unsupported attachments count
    too, so a text reply about a video cannot accidentally satisfy a form while
    its referenced media is silently discarded.
    """
    return any(
        getattr(source, field, None)
        for source in (
            message,
            getattr(message, "reply_to_message", None),
            getattr(message, "external_reply", None),
        )
        if source is not None
        for field in _MEDIA_FIELDS
    )


def _byte_limit(kind: str) -> int:
    return {
        "image": attachments.MAX_BYTES,
        "audio": MAX_AUDIO_BYTES,
        "text": MAX_DOCUMENT_BYTES,
    }[kind]


def _size_error(kind: str) -> InputError:
    label = {"image": "Images", "audio": "Audio files", "text": "Text documents"}[kind]
    limit = {"image": "5 MiB", "audio": "20 MiB", "text": "128 KiB"}[kind]
    return InputError("file_too_large", f"{label} must be no larger than {limit} each.")


def _check_size(size: int, kind: str, limit: int) -> None:
    if size <= limit:
        return
    if limit == _byte_limit(kind):
        raise _size_error(kind)
    raise InputError(
        "turn_too_large", "Attachments must total no more than 20 MiB per turn."
    )


def _media_ref(kind: Literal["image", "audio", "text"], file: Any) -> MediaRef:
    size = getattr(file, "file_size", None)
    if size is not None and size > _byte_limit(kind):
        raise _size_error(kind)
    duration = getattr(file, "duration", None)
    if isinstance(duration, timedelta):
        duration = duration.total_seconds()
    if kind == "audio" and duration is not None and duration > MAX_AUDIO_SECONDS:
        raise InputError(
            "audio_too_long", "Audio messages must be 10 minutes or shorter."
        )
    return MediaRef(
        kind=kind,
        file_id=file.file_id,
        unique_id=getattr(file, "file_unique_id", "") or file.file_id,
        size=size,
        name=getattr(file, "file_name", "") or "",
        duration=duration,
    )


def _extract_media(message: Message | ExternalReplyInfo) -> MediaRef | None:
    if any(getattr(message, kind, None) is not None for kind in _UNSUPPORTED):
        raise InputError("unsupported_type", _SUPPORTED_MESSAGE)
    if message.photo:
        # Telegram exposes variants of ONE photo. Pick the most detailed variant
        # within our bound, rather than downloading every size or refusing a
        # photo for which Telegram already provides an acceptable smaller copy.
        candidates = [
            photo
            for photo in message.photo
            if photo.file_size is None or photo.file_size <= attachments.MAX_BYTES
        ]
        if not candidates:
            raise _size_error("image")
        return _media_ref(
            "image", max(candidates, key=lambda photo: photo.width * photo.height)
        )
    if message.sticker:
        if message.sticker.is_animated or message.sticker.is_video:
            raise InputError("unsupported_type", _SUPPORTED_MESSAGE)
        return _media_ref("image", message.sticker)
    if message.voice or message.audio:
        return _media_ref("audio", message.voice or message.audio)
    if message.document:
        file = message.document
        suffix = PurePosixPath(file.file_name or "").suffix.lower()
        if file.mime_type in attachments.ALLOWED or suffix in _IMAGE_SUFFIXES:
            return _media_ref("image", file)
        if (file.mime_type or "").startswith("audio/") or suffix in _AUDIO_SUFFIXES:
            return _media_ref("audio", file)
        if suffix in _TEXT_SUFFIXES:
            return _media_ref("text", file)
        raise InputError("unsupported_type", _SUPPORTED_MESSAGE)
    return None


def _check_text(text: str) -> None:
    if len(text) > MAX_TEXT_CHARS:
        raise InputError(
            "text_too_long",
            "The message and attached text must total no more than 32,768 characters.",
        )


def extract_input(messages: Sequence[Message]) -> TelegramInput:
    """Extract one message or one complete album, including one-level replies.

    Incoming messages and quoted media are deduplicated independently. A quoted
    photo remains visible even when the current message carries its own image.
    Only actual Telegram attachments are selected; URLs and quoted text never
    cause network requests.
    """
    if not messages:
        raise InputError("empty_input", _SUPPORTED_MESSAGE)
    ordered = sorted(messages, key=lambda item: item.message_id)
    first = ordered[0]
    if len(ordered) > 1 and (
        not first.media_group_id
        or any(
            message.chat_id != first.chat_id
            or message.media_group_id != first.media_group_id
            for message in ordered
        )
    ):
        raise InputError(
            "invalid_album", "Please send one message or photo album at a time."
        )
    texts: list[str] = []
    quoted_texts: list[str] = []
    media: dict[str, MediaRef] = {}
    direct_ids = {(message.chat_id, message.message_id) for message in ordered}
    seen_messages: set[tuple[int, int]] = set()
    seen_quotes: set[tuple[int, int]] = set()
    message_ids: list[int] = []

    def collect(message: Message | ExternalReplyInfo, *, quoted: bool) -> None:
        ref = _extract_media(message)
        if ref:
            media.setdefault(ref.unique_id, ref)
        value = (
            getattr(message, "text", None) or getattr(message, "caption", None) or ""
        )
        if value.strip():
            (quoted_texts if quoted else texts).append(value)
        if not value.strip() and not ref:
            raise InputError("unsupported_type", _SUPPORTED_MESSAGE)

    for message in ordered:
        identity = (message.chat_id, message.message_id)
        if identity in seen_messages:
            continue
        seen_messages.add(identity)
        message_ids.append(message.message_id)
        collect(message, quoted=False)
        reply = message.reply_to_message
        if reply:
            identity = (reply.chat_id, reply.message_id)
            if identity not in seen_quotes and identity not in direct_ids:
                seen_quotes.add(identity)
                collect(reply, quoted=True)
        elif message.external_reply:
            # External replies may expose media without a full Message. Its
            # origin alone is not enough context to answer about hidden content.
            collect(message.external_reply, quoted=True)

    if len(media) > MAX_FILES_PER_TURN:
        raise InputError(
            "too_many_files",
            f"Send at most {MAX_FILES_PER_TURN} attachments per turn, including replied-to media.",
        )
    if sum(ref.size or 0 for ref in media.values()) > MAX_TOTAL_BYTES:
        raise InputError(
            "turn_too_large", "Attachments must total no more than 20 MiB per turn."
        )
    if quoted_texts:
        texts.append("Replied-to message:\n" + "\n\n".join(quoted_texts))
    text = "\n\n".join(texts)
    _check_text(text)
    return TelegramInput(text, tuple(media.values()), tuple(message_ids))


def _validate_image(data: bytes) -> str:
    mime = attachments.sniff(data)
    if mime is None:
        raise InputError(
            "invalid_image", "The attachment is not a PNG, JPEG, GIF or WebP image."
        )
    try:
        with Image.open(io.BytesIO(data)) as picture:
            width, height = picture.size
            if min(width, height) <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise InputError(
                    "image_too_large",
                    "Images must contain no more than 25 million pixels.",
                )
            if getattr(picture, "is_animated", False):
                raise InputError(
                    "unsupported_type",
                    "Send a static image; animated images are not supported.",
                )
            picture.verify()
        with Image.open(io.BytesIO(data)) as picture:
            picture.load()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise InputError(
            "invalid_image",
            "The image is damaged or cannot be decoded. Please send it again.",
        ) from None
    return mime


def _decode_document(data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InputError(
            "invalid_document", "Text documents must use UTF-8 encoding."
        ) from None
    if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
        raise InputError(
            "invalid_document",
            "The document contains binary data; send a UTF-8 text document.",
        )
    if not text.strip():
        raise InputError("empty_document", "The text document is empty.")
    _check_text(text)
    return text


class TelegramInputResolver:
    """A bounded downloader shared across turns; no payload or URL cache."""

    def __init__(self, bot: Any):
        self._bot = bot
        self._session: aiohttp.ClientSession | None = None
        self._downloads = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
        self._validations = asyncio.Semaphore(IMAGE_VALIDATION_CONCURRENCY)
        self._validation_workers: set[asyncio.Task[str]] = set()
        self._closed = False

    async def close(self) -> None:
        self._closed = True
        if self._session is not None:
            await self._session.close()
        if self._validation_workers:
            await asyncio.shield(
                asyncio.gather(*self._validation_workers, return_exceptions=True)
            )

    async def _validate(self, data: bytes) -> str:
        # Pixel decoding has a separate memory budget from network I/O. The
        # worker owns the slot because cancelling its caller cannot stop a
        # running Pillow decoder in the executor thread.
        await self._validations.acquire()
        if self._closed:
            self._validations.release()
            raise InputError(
                "service_unavailable",
                "Attachment handling is shutting down. Please try again shortly.",
            )
        worker = asyncio.create_task(asyncio.to_thread(_validate_image, data))
        self._validation_workers.add(worker)

        def finished(task: asyncio.Task[str]) -> None:
            self._validation_workers.discard(task)
            self._validations.release()
            if not task.cancelled():
                task.exception()

        worker.add_done_callback(finished)
        return await asyncio.shield(worker)

    async def _download(self, ref: MediaRef, remaining: int) -> bytes:
        limit = min(_byte_limit(ref.kind), remaining)
        if limit <= 0:
            raise InputError(
                "turn_too_large", "Attachments must total no more than 20 MiB per turn."
            )
        try:
            async with asyncio.timeout(DOWNLOAD_TIMEOUT_SECONDS):
                async with self._downloads:
                    file = await self._bot.get_file(
                        ref.file_id,
                        read_timeout=15,
                        write_timeout=15,
                        connect_timeout=10,
                        pool_timeout=10,
                    )
                    if file.file_size is not None:
                        _check_size(file.file_size, ref.kind, limit)
                    url = file.file_path
                    if not url or urlsplit(url).scheme not in {"http", "https"}:
                        raise InputError(
                            "download_failed",
                            "Telegram did not provide a downloadable file. Please send it again.",
                        )
                    if self._closed:
                        raise InputError(
                            "download_failed",
                            "Attachment downloads are shutting down. Please try again shortly.",
                        )
                    if self._session is None:
                        self._session = aiohttp.ClientSession(
                            timeout=aiohttp.ClientTimeout(
                                total=30, connect=10, sock_read=15
                            ),
                            connector=aiohttp.TCPConnector(limit=DOWNLOAD_CONCURRENCY),
                            trust_env=True,
                            auto_decompress=False,
                        )
                    async with self._session.get(
                        url, allow_redirects=False
                    ) as response:
                        if response.status != 200:
                            raise InputError(
                                "download_failed",
                                "Could not download the attachment from Telegram. Please send it again.",
                            )
                        if response.content_length is not None:
                            _check_size(response.content_length, ref.kind, limit)
                        buffer = bytearray()
                        async for chunk in response.content.iter_chunked(_CHUNK_BYTES):
                            _check_size(len(buffer) + len(chunk), ref.kind, limit)
                            buffer.extend(chunk)
                        if not buffer:
                            raise InputError(
                                "empty_file",
                                "The attachment is empty. Please send it again.",
                            )
                        return bytes(buffer)
        except InputError:
            raise
        except Exception:
            # Exceptions from Telegram and HTTP libraries may embed the complete
            # token-bearing file URL. Neither callers nor logs should receive it.
            raise InputError(
                "download_failed",
                "Could not download the attachment from Telegram. Please send it again.",
            ) from None

    async def resolve(
        self,
        turn: TelegramInput,
        *,
        language: str | None = None,
        model_size: str = "base",
    ) -> ResolvedInput:
        text_parts = [turn.text] if turn.text else []
        images: list[PromptImage] = []
        transcriptions: list[str] = []
        remaining = MAX_TOTAL_BYTES
        for ref in turn.media:
            data = await self._download(ref, remaining)
            remaining -= len(data)
            if ref.kind == "image":
                mime = await self._validate(data)
                images.append(PromptImage(data=data, mime=mime))
            elif ref.kind == "text":
                text_parts.append("Attached text document:\n" + _decode_document(data))
            else:
                from utils.transcribe import AudioTooLongError, transcribe_voice

                try:
                    transcript = await transcribe_voice(
                        data, language=language, model_size=model_size
                    )
                except AudioTooLongError:
                    raise InputError(
                        "audio_too_long",
                        "Audio messages must be 10 minutes or shorter.",
                    ) from None
                except Exception:
                    raise InputError(
                        "transcription_failed",
                        "Could not transcribe the audio. Please try again or send text.",
                    ) from None
                if not transcript or not transcript.strip():
                    raise InputError(
                        "empty_transcription",
                        "No speech could be transcribed from the audio. Please try again or send text.",
                    )
                transcript = transcript.strip()
                transcriptions.append(transcript)
                text_parts.append(transcript)
            _check_text("\n\n".join(text_parts))
        return ResolvedInput(
            "\n\n".join(text_parts), tuple(images), tuple(transcriptions)
        )


_resolver: TelegramInputResolver | None = None
_resolver_closed = False


def get_resolver(bot: Any) -> TelegramInputResolver:
    """Keep the application-owned client outside reloadable handler modules."""
    global _resolver
    if _resolver_closed:
        raise InputError("shutdown", "The bot is restarting. Please try again shortly.")
    if _resolver is None:
        _resolver = TelegramInputResolver(bot)
    elif _resolver._bot is not bot:
        raise InputError(
            "service_unavailable",
            "Attachment handling is restarting. Please try again shortly.",
        )
    return _resolver


async def close_resolver() -> None:
    global _resolver_closed
    _resolver_closed = True
    if _resolver is not None:
        await _resolver.close()
