"""Generated photos must be decodable, bounded, and acknowledged by Telegram."""

import asyncio
import io
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from telegram.error import BadRequest, RetryAfter, TelegramError, TimedOut

from condor import paths
from condor.notifications import Notification, NotifyBot
from condor.runtime.attachments import MAX_BYTES
from condor.telegram_media import load_photo, send_photo, validate_photo


def _picture(format="PNG", size=(40, 30)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "navy").save(buffer, format=format)
    return buffer.getvalue()


@pytest.mark.parametrize("format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_valid_photo_is_read_by_content_not_extension(tmp_path, format, mime):
    data = _picture(format)
    target = tmp_path / "misleading.txt"
    target.write_bytes(data)

    assert validate_photo(data) == mime
    assert load_photo(str(target)) == (data, mime)


@pytest.mark.parametrize("format", ["GIF", "WEBP", "BMP"])
def test_other_image_formats_require_explicit_conversion(format):
    with pytest.raises(ValueError, match="static PNG or JPEG"):
        validate_photo(_picture(format))


@pytest.mark.parametrize(
    "data", [b"", b"not an image", b"\x89PNG\r\n\x1a\n" + b"\0" * 64]
)
def test_empty_and_magic_number_only_files_are_not_photos(data):
    with pytest.raises(ValueError):
        validate_photo(data)


@pytest.mark.parametrize("format", ["PNG", "JPEG"])
def test_truncated_image_is_refused_before_upload(format):
    with pytest.raises(ValueError, match="not a readable"):
        validate_photo(_picture(format)[:-20])


def test_animated_png_does_not_silently_become_a_still():
    buffer = io.BytesIO()
    first = Image.new("RGB", (40, 30), "navy")
    second = Image.new("RGB", (40, 30), "red")
    first.save(
        buffer, format="PNG", save_all=True, append_images=[second], duration=100
    )

    with pytest.raises(ValueError, match="static PNG"):
        validate_photo(buffer.getvalue())


@pytest.mark.parametrize("size", [(10000, 1), (1, 10000), (21, 1), (1, 21)])
def test_telegram_dimension_constraints_are_checked_locally(size):
    with pytest.raises(ValueError, match="must not exceed"):
        validate_photo(_picture(size=size))


def test_telegram_maximum_aspect_ratio_is_accepted():
    assert validate_photo(_picture(size=(20, 1))) == "image/png"


def test_large_file_is_refused_without_reading_its_contents(tmp_path, monkeypatch):
    target = tmp_path / "large.png"
    with target.open("wb") as handle:
        handle.truncate(MAX_BYTES + 1)
    monkeypatch.setattr(
        "condor.telegram_media.os.fdopen",
        lambda *a, **k: pytest.fail("read oversized file"),
    )

    with pytest.raises(ValueError, match="5 MiB"):
        load_photo(str(target))


def test_byte_limit_applies_even_when_data_did_not_come_from_a_file():
    with pytest.raises(ValueError, match="5 MiB"):
        validate_photo(b"x" * (MAX_BYTES + 1))


def test_relative_paths_use_the_project_root_not_current_directory(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    project.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    data = _picture()
    (project / "chart.png").write_bytes(data)
    monkeypatch.setattr(paths, "_PROJECT_ROOT", project)
    monkeypatch.chdir(other)

    assert load_photo("chart.png") == (data, "image/png")


def test_nonregular_files_cannot_block_or_stream_unbounded_data(tmp_path):
    fifo = tmp_path / "image-pipe"
    os.mkfifo(fifo)
    for target in (fifo, tmp_path):
        with pytest.raises(ValueError, match="regular file"):
            load_photo(str(target))


def test_missing_file_is_a_clear_validation_error(tmp_path):
    with pytest.raises(ValueError, match="Could not read image"):
        load_photo(str(tmp_path / "missing.png"))


class _Bot:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.uploads = []

    async def send_photo(self, **kwargs):
        photo = kwargs["photo"]
        self.uploads.append((kwargs["chat_id"], photo.name, photo.read()))
        photo.close()
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.parametrize(
    "format,mime,filename",
    [("PNG", "image/png", "chart.png"), ("JPEG", "image/jpeg", "chart.jpg")],
)
@pytest.mark.parametrize(
    "receipt",
    [{"ok": True, "result": {"message_id": 7}}, SimpleNamespace(message_id=7)],
)
def test_upload_uses_correct_filename_and_returns_real_receipt(
    format, mime, filename, receipt
):
    data = _picture(format)
    bot = _Bot([receipt])

    assert asyncio.run(send_photo(bot, 42, data, mime)) is receipt
    assert bot.uploads == [(42, filename, data)]


@pytest.mark.parametrize(
    "rate_limit", [RetryAfter(1), {"ok": False, "parameters": {"retry_after": 1}}]
)
def test_rate_limit_retries_use_fresh_full_streams(monkeypatch, rate_limit):
    monkeypatch.setattr("condor.telegram_text.asyncio.sleep", AsyncMock())
    receipt = {"ok": True, "result": {"message_id": 7}}
    data = _picture()
    bot = _Bot([rate_limit, receipt])

    assert asyncio.run(send_photo(bot, 42, data, "image/png")) == receipt
    assert bot.uploads == [(42, "chart.png", data)] * 2


def test_rate_limit_retries_are_bounded(monkeypatch):
    monkeypatch.setattr("condor.telegram_text.asyncio.sleep", AsyncMock())
    bot = _Bot([RetryAfter(1)] * 3)

    with pytest.raises(RetryAfter):
        asyncio.run(send_photo(bot, 42, _picture(), "image/png"))
    assert len(bot.uploads) == 3


@pytest.mark.parametrize(
    "failure",
    [
        BadRequest("photo rejected"),
        TimedOut("response lost"),
        {"ok": False, "description": "photo rejected"},
    ],
)
def test_rejections_and_unconfirmed_delivery_are_never_replayed(failure):
    bot = _Bot([failure])

    with pytest.raises(TelegramError):
        asyncio.run(send_photo(bot, 42, _picture(), "image/png"))
    assert len(bot.uploads) == 1


@pytest.mark.parametrize(
    "receipt",
    [
        None,
        True,
        {},
        {"ok": True},
        {"ok": True, "result": {"message_id": 0}},
        Notification("n", 42, 0, "agent", "chart"),
    ],
)
def test_nonreceipts_never_claim_telegram_success(receipt):
    with pytest.raises(TelegramError, match="did not acknowledge"):
        asyncio.run(send_photo(_Bot([receipt]), 42, _picture(), "image/png"))


def test_dashboard_bot_is_rejected_before_it_records_a_caption(monkeypatch):
    bot = NotifyBot()
    bot.send_photo = AsyncMock()

    with pytest.raises(TelegramError, match="not configured"):
        asyncio.run(send_photo(bot, 42, _picture(), "image/png"))
    bot.send_photo.assert_not_awaited()


def test_http_bot_preserves_upload_name_mime_and_caption_mode():
    from condor.routine_store import _HttpBot

    bot = _HttpBot()
    bot._post = AsyncMock(return_value={"ok": True, "result": {"message_id": 7}})
    photo = io.BytesIO(_picture("JPEG"))
    photo.name = "generated.jpg"

    asyncio.run(
        bot.send_photo(
            chat_id=42, photo=photo, caption="<b>Chart</b>", parse_mode="HTML"
        )
    )

    bot._post.assert_awaited_once_with(
        "sendPhoto",
        {"chat_id": 42, "caption": "<b>Chart</b>", "parse_mode": "HTML"},
        files={"photo": ("generated.jpg", photo, "image/jpeg")},
    )


def test_http_bot_explicit_filename_overrides_the_buffer_name():
    from condor.routine_store import _HttpBot

    bot = _HttpBot()
    bot._post = AsyncMock()
    photo = io.BytesIO(_picture())
    photo.name = "wrong.jpg"

    asyncio.run(bot.send_photo(42, photo, filename="generated.png"))

    assert bot._post.await_args.kwargs["files"]["photo"] == (
        "generated.png",
        photo,
        "image/png",
    )


def test_http_bot_accepts_temporary_file_with_descriptor_name():
    from condor.routine_store import _HttpBot

    bot = _HttpBot()
    bot._post = AsyncMock()
    with tempfile.TemporaryFile() as photo:
        photo.write(_picture())
        photo.seek(0)
        assert isinstance(photo.name, int)

        asyncio.run(bot.send_photo(42, photo))

        assert bot._post.await_args.kwargs["files"]["photo"] == (
            "chart.png",
            photo,
            "image/png",
        )


@pytest.mark.parametrize("name", [b"generated.jpg", object(), 42])
def test_http_bot_ignores_nonpath_filelike_names(name):
    from condor.routine_store import _HttpBot

    bot = _HttpBot()
    bot._post = AsyncMock()
    photo = io.BytesIO(_picture())
    photo.name = name

    asyncio.run(bot.send_photo(42, photo))

    assert bot._post.await_args.kwargs["files"]["photo"] == (
        "chart.png",
        photo,
        "image/png",
    )


def test_http_bot_normalizes_pathlike_filename_to_text():
    from condor.routine_store import _HttpBot

    bot = _HttpBot()
    bot._post = AsyncMock()
    photo = io.BytesIO(_picture("JPEG"))

    asyncio.run(bot.send_photo(42, photo, filename=Path("generated.jpg")))

    assert bot._post.await_args.kwargs["files"]["photo"] == (
        "generated.jpg",
        photo,
        "image/jpeg",
    )
