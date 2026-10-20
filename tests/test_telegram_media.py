"""Generated photos must be decodable, bounded, and acknowledged by Telegram."""

import asyncio
import io
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import BufferedInputFile
from PIL import Image

from condor import paths
from condor.notifications import Notification, NotifyBot
from condor.runtime.attachments import MAX_BYTES
from condor.telegram.errors import (
    BadRequest,
    RateLimited,
    TelegramFailure,
    bad_request,
    network_failure,
    rate_limited,
)
from condor.telegram.media import load_photo, send_photo, validate_photo
from condor.telegram.transport import as_input_file


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
        "condor.telegram.media.os.fdopen",
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
        assert isinstance(photo, BufferedInputFile)
        self.uploads.append((kwargs["chat_id"], photo.filename, photo.data))
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.parametrize(
    "format,mime,filename",
    [("PNG", "image/png", "chart.png"), ("JPEG", "image/jpeg", "chart.jpg")],
)
def test_upload_uses_correct_filename_and_returns_real_receipt(format, mime, filename):
    data = _picture(format)
    receipt = SimpleNamespace(message_id=7)
    bot = _Bot([receipt])

    assert asyncio.run(send_photo(bot, 42, data, mime)) is receipt
    assert bot.uploads == [(42, filename, data)]


def test_rate_limit_retries_use_fresh_full_streams(monkeypatch):
    monkeypatch.setattr("condor.telegram.transport.asyncio.sleep", AsyncMock())
    receipt = SimpleNamespace(message_id=7)
    data = _picture()
    bot = _Bot([rate_limited(1), receipt])

    assert asyncio.run(send_photo(bot, 42, data, "image/png")) is receipt
    assert bot.uploads == [(42, "chart.png", data)] * 2


def test_rate_limit_retries_are_bounded(monkeypatch):
    monkeypatch.setattr("condor.telegram.transport.asyncio.sleep", AsyncMock())
    bot = _Bot([rate_limited(1)] * 3)

    with pytest.raises(RateLimited):
        asyncio.run(send_photo(bot, 42, _picture(), "image/png"))
    assert len(bot.uploads) == 3


@pytest.mark.parametrize(
    "failure",
    [bad_request("photo rejected"), network_failure("response lost")],
)
def test_rejections_and_unconfirmed_delivery_are_never_replayed(failure):
    bot = _Bot([failure])

    with pytest.raises(TelegramFailure):
        asyncio.run(send_photo(bot, 42, _picture(), "image/png"))
    assert len(bot.uploads) == 1


@pytest.mark.parametrize(
    "receipt",
    [
        None,
        True,
        {},
        SimpleNamespace(message_id=0),
        Notification("n", 42, 0, "agent", "chart"),
    ],
)
def test_nonreceipts_never_claim_telegram_success(receipt):
    with pytest.raises(TelegramFailure, match="did not acknowledge"):
        asyncio.run(send_photo(_Bot([receipt]), 42, _picture(), "image/png"))


def test_dashboard_bot_is_rejected_before_it_records_a_caption(monkeypatch):
    bot = NotifyBot()
    bot.send_photo = AsyncMock()

    with pytest.raises(TelegramFailure, match="not configured"):
        asyncio.run(send_photo(bot, 42, _picture(), "image/png"))
    bot.send_photo.assert_not_awaited()


def test_as_input_file_keeps_the_buffer_name():
    photo = io.BytesIO(_picture("JPEG"))
    photo.name = "generated.jpg"

    upload = as_input_file(photo)

    assert isinstance(upload, BufferedInputFile)
    assert (upload.filename, upload.data) == ("generated.jpg", photo.getvalue())


def test_as_input_file_explicit_filename_overrides_the_buffer_name():
    photo = io.BytesIO(_picture())
    photo.name = "wrong.jpg"

    assert as_input_file(photo, filename="generated.png").filename == "generated.png"


@pytest.mark.parametrize("name", [b"generated.jpg", object(), 42])
def test_as_input_file_ignores_nonpath_filelike_names(name):
    photo = io.BytesIO(_picture())
    photo.name = name

    assert as_input_file(photo).filename == "file"


def test_as_input_file_uses_only_the_basename_of_a_path():
    photo = io.BytesIO(_picture())
    photo.name = "/tmp/reports/generated.png"

    assert as_input_file(photo).filename == "generated.png"


def test_as_input_file_wraps_bytes_and_passes_through_the_rest():
    assert as_input_file(b"abc", filename="a.bin").data == b"abc"
    assert as_input_file("AgACAgIAAxkBAAI") == "AgACAgIAAxkBAAI"
    ready = BufferedInputFile(b"x", filename="x.png")
    assert as_input_file(ready) is ready


def test_bad_request_factory_reads_like_a_telegram_rejection():
    assert isinstance(bad_request("photo rejected"), BadRequest)
    assert "photo rejected" in str(bad_request("photo rejected"))
