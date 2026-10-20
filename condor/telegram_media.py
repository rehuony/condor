"""Validate generated photos and deliver them with a real Telegram receipt.

Local-file authorization belongs to the caller. The notification route requires
the same grant as ``run_code`` before reaching :func:`load_photo`; this module
only owns the bounded read, image constraints, and Telegram upload mechanics.
"""

from __future__ import annotations

import io
import os
import stat
from pathlib import Path
from typing import Any

from PIL import Image
from telegram.error import TelegramError

from condor import paths
from condor.runtime.attachments import MAX_BYTES
from condor.telegram_text import telegram_call

_MIMES = {"PNG": "image/png", "JPEG": "image/jpeg"}
_FILENAMES = {"image/png": "chart.png", "image/jpeg": "chart.jpg"}


def validate_photo(data: bytes) -> str:
    """Return a PNG/JPEG MIME type, refusing corrupt or unsupported photos.

    The conversation attachment limit is stricter than Telegram's byte limit.
    Telegram additionally limits width + height to 10000 and the aspect ratio
    to 20: https://core.telegram.org/bots/api#sendphoto.
    """
    if not data:
        raise ValueError("Image file is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError(f"Image exceeds the {MAX_BYTES}-byte limit (5 MiB).")

    try:
        with Image.open(io.BytesIO(data)) as picture:
            mime = _MIMES.get(picture.format)
            if mime is None or getattr(picture, "is_animated", False):
                raise ValueError("Send a static PNG or JPEG image.")
            width, height = picture.size
            if min(width, height) <= 0 or width + height > 10000:
                raise ValueError("Image width plus height must not exceed 10000.")
            if max(width, height) > 20 * min(width, height):
                raise ValueError("Image width-to-height ratio must not exceed 20.")
            picture.verify()
        # ``verify`` checks container integrity; JPEG in particular also needs
        # a decode to catch a truncated pixel stream before anything is sent.
        with Image.open(io.BytesIO(data)) as picture:
            picture.load()
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("Image is not a readable PNG or JPEG.") from exc
    return mime


def load_photo(path: str) -> tuple[bytes, str]:
    """Read one regular local file, bounded before and during the read.

    Relative paths use the project root, shared by the agent's file tools and
    the main process, rather than whichever working directory runs this call.
    The caller must authorize local-file access before invoking this function.
    """
    if not path or not path.strip():
        raise ValueError("image_path is required.")
    try:
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = paths._PROJECT_ROOT / target
        # Opening a FIFO must not block the event loop before fstat can refuse
        # it. Checking the opened descriptor also avoids a stat/open race.
        fd = os.open(target, os.O_RDONLY | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("image_path must name a regular file.")
            if info.st_size > MAX_BYTES:
                raise ValueError(f"Image exceeds the {MAX_BYTES}-byte limit (5 MiB).")
            with os.fdopen(fd, "rb", closefd=False) as source:
                data = source.read(MAX_BYTES + 1)
        finally:
            os.close(fd)
    except OSError as exc:
        raise ValueError(f"Could not read image file: {exc.strerror or exc}") from exc
    except RuntimeError as exc:
        raise ValueError(f"Could not resolve image path: {exc}") from exc
    return data, validate_photo(data)


async def send_photo(bot: Any, chat_id: int, data: bytes, mime: str) -> Any:
    """Upload a validated photo, retrying only Telegram's explicit rate limit.

    No caption is sent: the notification's prose uses the existing text pager,
    so its length and formatting cannot violate Telegram's caption limit.
    A failed or unconfirmed upload raises; a dashboard bell is not a receipt.
    """
    from condor.notifications import NotifyBot

    if isinstance(bot, NotifyBot):
        raise TelegramError("Telegram is not configured for image delivery")
    filename = _FILENAMES.get(mime)
    if filename is None:
        raise ValueError("Send a static PNG or JPEG image.")

    async def upload():
        # A transport may consume or close its input even when it rejects the
        # request. Every RetryAfter attempt therefore gets a fresh full stream.
        with io.BytesIO(data) as photo:
            photo.name = filename
            return await bot.send_photo(chat_id=chat_id, photo=photo)

    result = await telegram_call(upload)
    if isinstance(result, dict):
        message = result.get("result") if result.get("ok") is True else None
        message_id = message.get("message_id") if isinstance(message, dict) else None
    else:
        message_id = getattr(result, "message_id", None)
    if type(message_id) is not int or message_id <= 0:
        raise TelegramError("Telegram did not acknowledge the image delivery")
    return result
