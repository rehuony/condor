"""Telegram ingress keeps attachments intact and bounds work before prompting."""

import asyncio
import io
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import (
    Audio,
    Chat,
    Document,
    ExternalReplyInfo,
    Message,
    MessageOriginHiddenUser,
    PhotoSize,
    Sticker,
    Story,
    Voice,
)
from PIL import Image

from condor.runtime import attachments
from condor.telegram import input as ingress


def message(message_id=1, **kwargs):
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=10, type="private"),
        **kwargs,
    )


def photo(file_id="photo", unique_id="unique-photo", **kwargs):
    return PhotoSize(
        file_id=file_id,
        file_unique_id=unique_id,
        width=kwargs.pop("width", 100),
        height=kwargs.pop("height", 100),
        **kwargs,
    )


def image_bytes(format="PNG"):
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format=format)
    return buffer.getvalue()


def rich_message(blocks, **fields):
    return Message.model_validate(
        {
            "message_id": 1,
            "date": 1,
            "chat": {"id": 10, "type": "private"},
            "rich_message": {"blocks": blocks},
            **fields,
        },
    )


_FILE = {"file_id": "f", "file_unique_id": "u"}
_RICH_MEDIA = {
    "photo": {"photo": [{**_FILE, "width": 10, "height": 10}]},
    "video": {"video": {**_FILE, "width": 10, "height": 10, "duration": 1}},
    "animation": {"animation": {**_FILE, "width": 10, "height": 10, "duration": 1}},
    "audio": {"audio": {**_FILE, "duration": 1}},
    "voice_note": {"voice_note": {**_FILE, "duration": 1}},
    "document": {"document": _FILE},
    "collage": {
        "blocks": [{"type": "photo", "photo": [{**_FILE, "width": 10, "height": 10}]}]
    },
    "slideshow": {
        "blocks": [{"type": "photo", "photo": [{**_FILE, "width": 10, "height": 10}]}]
    },
    "map": {
        "location": {"latitude": 1.0, "longitude": 2.0},
        "zoom": 10,
        "width": 100,
        "height": 100,
    },
}


def rich_media_block(kind):
    """A valid Bot API media block of ``kind``."""
    return {"type": kind, **_RICH_MEDIA[kind]}


def test_rich_message_keeps_paragraphs_lists_tables_quotes_and_code():
    incoming = rich_message(
        [
            {
                "type": "paragraph",
                "text": [
                    "Compare ",
                    {
                        "type": "bold",
                        "text": [
                            "BTC and ",
                            {
                                "type": "url",
                                "text": {"type": "italic", "text": "ETH"},
                                "url": "https://example.test",
                            },
                        ],
                    },
                ],
            },
            {
                "type": "list",
                "items": [
                    {
                        "label": "•",
                        "blocks": [
                            {"type": "paragraph", "text": "First"},
                            {
                                "type": "list",
                                "items": [
                                    {
                                        "label": "1.",
                                        "blocks": [
                                            {"type": "paragraph", "text": "Nested"}
                                        ],
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "label": "2.",
                        "blocks": [{"type": "paragraph", "text": "Second"}],
                    },
                    {
                        "label": "",
                        "has_checkbox": True,
                        "blocks": [{"type": "paragraph", "text": "Pending"}],
                    },
                    {
                        "label": "",
                        "has_checkbox": True,
                        "is_checked": True,
                        "blocks": [{"type": "paragraph", "text": "Done"}],
                    },
                ],
            },
            {
                "type": "table",
                "cells": [
                    [
                        {"text": "Asset", "align": "left", "valign": "top"},
                        {"text": "Price", "align": "right", "valign": "top"},
                    ],
                    [
                        {"text": "BTC", "align": "left", "valign": "top"},
                        {"text": "100", "align": "right", "valign": "top"},
                    ],
                ],
            },
            {
                "type": "blockquote",
                "blocks": [{"type": "paragraph", "text": "Quoted\nsecond line"}],
                "credit": "Analyst",
            },
            {"type": "pre", "language": "python", "text": "print('BTC')"},
        ]
    )
    assert incoming.text is None
    assert ingress.rich_message_payload(incoming) == incoming.rich_message.model_dump(
        exclude_none=True
    )
    assert ingress.extract_input([incoming]).text == (
        "Compare BTC and ETH\n\n"
        "- First\n  \n  1. Nested\n2. Second\n- [ ] Pending\n- [x] Done\n\n"
        "| Asset | Price |\n| --- | --- |\n| BTC | 100 |\n\n"
        "> Quoted\n> second line\n> — Analyst\n\n"
        "```python\nprint('BTC')\n```"
    )
    assert not ingress.input_has_attachments(incoming)


@pytest.mark.parametrize(
    "block,expected",
    [
        ({"type": "heading", "size": 6, "text": "Title"}, "###### Title"),
        ({"type": "heading", "size": 10, "text": "Title"}, "###### Title"),
        (
            {"type": "expandable_blockquote", "text": "Quote", "credit": "A"},
            "> Quote\n> — A",
        ),
        ({"type": "pullquote", "text": "Quote", "credit": "B"}, "> Quote\n> — B"),
        ({"type": "footer", "text": "Footer"}, "Footer"),
        ({"type": "thinking", "text": "Thinking"}, "Thinking"),
        ({"type": "anchor", "name": "section"}, "section"),
        ({"type": "divider"}, "---"),
        ({"type": "mathematical_expression", "expression": "x^2"}, "$x^2$"),
        (
            {
                "type": "details",
                "summary": {"type": "bold", "text": "Summary"},
                "blocks": [{"type": "paragraph", "text": "Body"}],
            },
            "Summary\n\nBody",
        ),
        ({"type": "pre", "text": "```md\ncode\n```"}, "````\n```md\ncode\n```\n````"),
    ],
)
def test_other_rich_blocks_preserve_text(block, expected):
    assert ingress.extract_input([rich_message([block])]).text == expected


@pytest.mark.parametrize(
    "block,expected",
    [
        (
            {
                "type": "future_block",
                "content": [{"title": "Future ", "value": "text"}],
            },
            "Future text",
        ),
        (
            {
                "type": "future_block",
                "text": {"type": "future_style", "text": "Visible"},
            },
            "Visible",
        ),
        ({"type": "future_empty"}, ""),
    ],
)
def test_unknown_block_types_keep_their_visible_text(block, expected):
    """The flattener reads plain payloads, so a block type newer than the
    library's models still yields whatever text it carries."""
    assert ingress.flatten_rich_message({"blocks": [block]}) == expected


def test_rich_inline_fallbacks_math_emoji_and_ignored_buttons():
    incoming = rich_message(
        [
            {
                "type": "paragraph",
                "text": [
                    {"type": "url", "text": "", "url": "https://example.test"},
                    " ",
                    {
                        "type": "email_address",
                        "text": "",
                        "email_address": "a@example.test",
                    },
                    " ",
                    {
                        "type": "custom_emoji",
                        "custom_emoji_id": "123",
                        "alternative_text": "👍",
                    },
                    " ",
                    {"type": "mathematical_expression", "expression": "x+1"},
                ],
            },
            {"type": "buttons", "buttons": [{"text": "Ignore this"}]},
        ]
    )
    assert (
        ingress.extract_input([incoming]).text
        == "https://example.test a@example.test 👍 $x+1$"
    )


def test_rich_table_escapes_delimiters_and_pads_short_rows():
    incoming = rich_message(
        [
            {
                "type": "table",
                "caption": "Prices",
                "cells": [
                    [
                        {"text": "A|B", "align": "left", "valign": "top"},
                        {"text": "C\nD", "align": "left", "valign": "top"},
                    ],
                    [{"align": "left", "valign": "top"}],
                ],
            }
        ]
    )
    assert (
        ingress.extract_input([incoming]).text
        == "Prices\n\n| A\\|B | C<br>D |\n| --- | --- |\n|  |  |"
    )


@pytest.mark.parametrize(
    "kind",
    [
        "photo",
        "video",
        "animation",
        "audio",
        "voice_note",
        "document",
        "collage",
        "slideshow",
        "map",
    ],
)
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("location", ["direct", "reply", "external"])
def test_rich_media_rejects_entire_turn_and_counts_as_an_attachment(
    kind, nested, location
):
    blocks = [rich_media_block(kind)]
    if nested:
        blocks = [
            {"type": "paragraph", "text": "Do not partially accept"},
            {"type": "details", "summary": "Media", "blocks": blocks},
        ]
    original = rich_message(blocks)
    incoming = original
    if location == "reply":
        incoming = message(2, text="Explain", reply_to_message=original)
    elif location == "external":
        incoming = message(
            text="Explain",
            external_reply=ExternalReplyInfo.model_validate(
                {
                    "origin": {
                        "type": "hidden_user",
                        "date": 1,
                        "sender_user_name": "Tester",
                    },
                    "rich_message": {"blocks": blocks},
                },
            ),
        )
    assert ingress.input_has_attachments(incoming)
    with pytest.raises(ingress.InputError) as failure:
        ingress.extract_input([incoming])
    assert failure.value.code == "unsupported_type"


def test_rich_text_limit_includes_quoted_text():
    incoming = rich_message(
        [{"type": "paragraph", "text": "a" * ingress.MAX_TEXT_CHARS}]
    )
    assert len(ingress.extract_input([incoming]).text) == ingress.MAX_TEXT_CHARS
    for oversized in [
        rich_message(
            [{"type": "paragraph", "text": "a" * (ingress.MAX_TEXT_CHARS + 1)}]
        ),
        message(2, text="More", reply_to_message=incoming),
    ]:
        with pytest.raises(ingress.InputError) as failure:
            ingress.extract_input([oversized])
        assert failure.value.code == "text_too_long"


def test_typed_rich_payload_and_text_caption_priority():
    from pydantic import BaseModel

    class RichPayload(BaseModel):
        blocks: list[dict]
        is_rtl: bool | None = None

    payload = {"blocks": [{"type": "paragraph", "text": "Rich"}]}
    source = SimpleNamespace(rich_message=RichPayload(**payload))
    assert ingress.rich_message_payload(source) == payload
    assert ingress.message_text(source) == "Rich"
    source.text, source.caption = "Text", "Caption"
    assert ingress.message_text(source) == "Text"
    source.text = ""
    assert ingress.message_text(source) == "Caption"
    source.rich_message = None
    source.caption = None
    assert ingress.message_text(source) == ""


@pytest.mark.parametrize("field", ["text", "caption"])
def test_original_text_whitespace_is_preserved(field):
    text = "\n    indented code\n      continued  "
    kwargs = {field: text}
    if field == "caption":
        kwargs["photo"] = [photo()]
    assert ingress.extract_input([message(**kwargs)]).text == text


def test_quote_already_present_later_in_album_is_not_duplicated():
    later = message(
        2, media_group_id="album", photo=[photo("two", "two")], caption="Second"
    )
    earlier = message(
        1,
        media_group_id="album",
        photo=[photo("one", "one")],
        caption="First",
        reply_to_message=later,
    )
    turn = ingress.extract_input([earlier, later])
    assert turn.text == "First\n\nSecond"
    assert [item.file_id for item in turn.media] == ["one", "two"]


@pytest.mark.parametrize("location", ["direct", "reply", "external"])
@pytest.mark.parametrize("supported", [True, False])
def test_attachment_predicate_includes_unsupported_and_referenced_media(
    location, supported
):
    kwargs = (
        {"photo": [photo()]}
        if supported
        else {
            "document": Document(
                file_id="doc", file_unique_id="doc", file_name="report.pdf"
            )
        }
    )
    if location == "direct":
        incoming = message(**kwargs)
    elif location == "reply":
        incoming = message(2, text="A form answer", reply_to_message=message(**kwargs))
    else:
        incoming = message(
            text="A form answer",
            external_reply=ExternalReplyInfo(
                origin=MessageOriginHiddenUser(
                    date=datetime.now(timezone.utc), sender_user_name="Someone"
                ),
                **kwargs,
            ),
        )
    assert ingress.input_has_attachments(incoming)


def test_attachment_predicate_does_not_follow_text_urls_or_recursive_replies():
    previous = message(
        text="https://example.test/image.png",
        reply_to_message=message(0, photo=[photo()]),
    )
    incoming = message(2, text="Form answer", reply_to_message=previous)
    assert not ingress.input_has_attachments(incoming)
    assert not ingress.input_has_attachments(
        message(text="https://example.test/image.png")
    )


def test_story_reference_is_detected_and_explicitly_refused():
    incoming = message(
        text="Explain",
        reply_to_message=message(
            0, story=Story(chat=Chat(id=10, type="private"), id=1)
        ),
    )
    assert ingress.input_has_attachments(incoming)
    with pytest.raises(ingress.InputError) as failure:
        ingress.extract_input([incoming])
    assert failure.value.code == "unsupported_type"


def test_caption_photo_and_reply_text_are_normalized():
    turn = ingress.extract_input(
        [
            message(
                photo=[photo()],
                caption="Analyse this",
                reply_to_message=message(0, text="Compare the support level"),
            )
        ]
    )
    assert turn.text == "Analyse this\n\nReplied-to message:\nCompare the support level"
    assert [item.file_id for item in turn.media] == ["photo"]
    assert turn.source_message_ids == (1,)


def test_reply_to_photo_retains_image_for_text_only_followup():
    turn = ingress.extract_input(
        [
            message(
                text="Explain this image",
                reply_to_message=message(0, photo=[photo()], caption="BTC/USDT"),
            )
        ]
    )
    assert turn.media[0].kind == "image"
    assert turn.media[0].file_id == "photo"
    assert turn.text == "Explain this image\n\nReplied-to message:\nBTC/USDT"


def test_only_largest_photo_variant_under_limit_is_selected():
    variants = [
        photo("small", width=50, file_size=100),
        photo("huge", width=3000, file_size=attachments.MAX_BYTES + 1),
        photo("large", width=2000, file_size=1000),
    ]
    assert ingress.extract_input([message(photo=variants)]).media[0].file_id == "large"


def test_album_orders_members_and_deduplicates_replied_media():
    reply = message(0, photo=[photo("ref", "ref")], caption="Reference")
    first = message(
        1,
        photo=[photo("one", "one")],
        caption="Compare",
        media_group_id="album",
        reply_to_message=reply,
    )
    second = message(
        2, photo=[photo("two", "two")], media_group_id="album", reply_to_message=reply
    )
    turn = ingress.extract_input([second, first, second])
    assert [item.file_id for item in turn.media] == ["one", "ref", "two"]
    assert turn.text.count("Reference") == 1
    assert turn.source_message_ids == (1, 2)


def test_replied_identical_image_is_only_downloaded_once():
    turn = ingress.extract_input(
        [
            message(
                photo=[photo("direct")],
                reply_to_message=message(0, photo=[photo("reply")]),
            )
        ]
    )
    assert len(turn.media) == 1


def test_reply_is_not_followed_recursively():
    old = message(0, photo=[photo()])
    parent = message(1, text="Earlier", reply_to_message=old)
    turn = ingress.extract_input([message(2, text="Now", reply_to_message=parent)])
    assert not turn.media
    assert turn.text == "Now\n\nReplied-to message:\nEarlier"


def test_external_reply_photo_is_available():
    reply = ExternalReplyInfo(
        origin=MessageOriginHiddenUser(
            date=datetime.now(timezone.utc), sender_user_name="Someone"
        ),
        photo=[photo()],
    )
    turn = ingress.extract_input([message(text="Explain", external_reply=reply)])
    assert turn.media[0].file_id == "photo"


@pytest.mark.parametrize(
    ("filename", "mime", "kind"),
    [
        ("chart.PNG", "application/octet-stream", "image"),
        ("data.bin", "image/webp", "image"),
        ("notes.md", "text/markdown", "text"),
        ("config.yaml", "application/yaml", "text"),
        ("memo.mp3", "application/octet-stream", "audio"),
    ],
)
def test_documents_are_classified_by_supported_type(filename, mime, kind):
    doc = Document(
        file_id="doc", file_unique_id="unique-doc", file_name=filename, mime_type=mime
    )
    assert ingress.extract_input([message(document=doc)]).media[0].kind == kind


@pytest.mark.parametrize("quoted", [False, True])
def test_unsupported_document_never_silently_becomes_text_only(quoted):
    unsupported = message(
        document=Document(
            file_id="doc", file_unique_id="unique", file_name="report.pdf"
        ),
        caption="Summarize",
    )
    incoming = (
        message(2, text="Summarize", reply_to_message=unsupported)
        if quoted
        else unsupported
    )
    with pytest.raises(ingress.InputError, match="PDFs"):
        ingress.extract_input([incoming])


def test_static_stickers_are_images_but_animated_stickers_are_refused():
    sticker = Sticker(
        file_id="sticker",
        file_unique_id="unique",
        width=100,
        height=100,
        type="regular",
        is_animated=False,
        is_video=False,
    )
    assert ingress.extract_input([message(sticker=sticker)]).media[0].kind == "image"
    animated = Sticker(
        file_id="sticker",
        file_unique_id="unique",
        width=100,
        height=100,
        type="regular",
        is_animated=True,
        is_video=False,
    )
    with pytest.raises(ingress.InputError):
        ingress.extract_input([message(sticker=animated)])


@pytest.mark.parametrize(
    "audio",
    [
        Voice(file_id="voice", file_unique_id="u", duration=601),
        Audio(file_id="audio", file_unique_id="u", duration=601),
    ],
)
def test_audio_duration_is_bounded_before_download(audio):
    kwargs = {"voice" if isinstance(audio, Voice) else "audio": audio}
    with pytest.raises(ingress.InputError, match="10 minutes"):
        ingress.extract_input([message(**kwargs)])


def test_known_file_size_and_turn_totals_fail_before_download():
    with pytest.raises(ingress.InputError, match="5 MiB"):
        ingress.extract_input(
            [message(photo=[photo(file_size=attachments.MAX_BYTES + 1)])]
        )
    with pytest.raises(ingress.InputError, match="20 MiB per turn"):
        ingress.extract_input(
            [
                message(
                    1,
                    audio=Audio(
                        file_id="one",
                        file_unique_id="one",
                        duration=3,
                        file_size=11 * 1024 * 1024,
                    ),
                    media_group_id="album",
                ),
                message(
                    2,
                    audio=Audio(
                        file_id="two",
                        file_unique_id="two",
                        duration=3,
                        file_size=11 * 1024 * 1024,
                    ),
                    media_group_id="album",
                ),
            ]
        )


def test_attachment_count_includes_reference_media():
    album = [
        message(index, media_group_id="album", photo=[photo(str(index), str(index))])
        for index in range(1, 5)
    ]
    album[0] = message(
        1,
        media_group_id="album",
        photo=[photo("1", "1")],
        reply_to_message=message(0, photo=[photo("ref", "ref")]),
    )
    with pytest.raises(ingress.InputError, match="at most 4"):
        ingress.extract_input(album)


def test_unrelated_messages_cannot_be_combined_as_an_album():
    with pytest.raises(ingress.InputError, match="one message"):
        ingress.extract_input([message(text="one"), message(2, text="two")])


class Response:
    def __init__(self, chunks, *, content_length=None, status=200):
        self.chunks = chunks
        self.status = status
        self.content_length = content_length
        self.content = self
        self.closed = False
        self.read_chunks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True

    async def iter_chunked(self, _size):
        for chunk in self.chunks:
            self.read_chunks += 1
            yield chunk


class Session:
    """Stands in for ``bot.session``: aiogram's file streaming entry point."""

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.api = SimpleNamespace(
            file_url=lambda token, path: f"https://api.telegram.org/file/bot{token}/{path}"
        )

    async def stream_content(self, url, **kwargs):
        self.requests.append((url, kwargs))
        response = next(self.responses)
        async with response:
            if response.status >= 400 and kwargs.get("raise_for_status"):
                raise RuntimeError(f"{response.status} for {url}")
            async for chunk in response.iter_chunked(kwargs.get("chunk_size")):
                yield chunk


def resolver(responses, *, file_size=None):
    bot = SimpleNamespace(
        token="SECRET",
        session=Session(responses),
        get_file=AsyncMock(
            return_value=SimpleNamespace(
                file_size=file_size, file_path="photos/image.png"
            )
        ),
    )
    return ingress.TelegramInputResolver(bot)


@pytest.mark.asyncio
async def test_photo_bytes_reach_prompt_images_with_caption():
    data = image_bytes()
    service = resolver([Response([data]), Response([data])])
    turn = ingress.extract_input([message(photo=[photo()], caption="Analyse")])
    for _ in range(2):
        resolved = await service.resolve(turn)
        assert resolved.text == "Analyse"
        assert resolved.images[0].data == data
        assert resolved.images[0].mime == "image/png"
        assert resolved.images[0].id == ""
    url, options = service._bot.session.requests[0]
    assert url == "https://api.telegram.org/file/botSECRET/photos/image.png"
    assert options["raise_for_status"] is True and options["timeout"] == 30
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["metadata", "chunks"])
async def test_download_size_limits_are_enforced_at_each_boundary(
    monkeypatch, boundary
):
    monkeypatch.setattr(attachments, "MAX_BYTES", 8)
    response = Response([b"12345", b"67890", b"never read"])
    service = resolver([response], file_size=9 if boundary == "metadata" else None)
    turn = ingress.extract_input([message(photo=[photo()])])
    with pytest.raises(ingress.InputError) as failure:
        await service.resolve(turn)
    assert failure.value.code == "file_too_large"
    if boundary == "metadata":
        assert not service._bot.session.requests
    else:
        assert response.closed
        assert response.read_chunks == 2


@pytest.mark.asyncio
async def test_remaining_turn_budget_is_enforced_during_chunked_download(monkeypatch):
    monkeypatch.setattr(ingress, "MAX_TOTAL_BYTES", 12)
    service = resolver([Response([b"first doc"]), Response([b"second doc"])])
    refs = tuple(
        ingress.MediaRef("text", str(index), str(index), None) for index in range(2)
    )
    with pytest.raises(ingress.InputError) as failure:
        await service.resolve(ingress.TelegramInput("", refs, (1, 2)))
    assert failure.value.code == "turn_too_large"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"\x89PNG\r\n\x1a\nnot an image", b"%PDF-1.7"])
async def test_mislabelled_or_corrupt_image_refuses_entire_turn(payload):
    service = resolver([Response([payload])])
    with pytest.raises(ingress.InputError) as failure:
        await service.resolve(
            ingress.extract_input([message(photo=[photo()], caption="Describe")])
        )
    assert failure.value.code == "invalid_image"


def test_image_pixel_limit_is_checked_before_decoding(monkeypatch):
    monkeypatch.setattr(ingress, "MAX_IMAGE_PIXELS", 3)
    with pytest.raises(ingress.InputError) as failure:
        ingress._validate_image(image_bytes())
    assert failure.value.code == "image_too_large"


@pytest.mark.parametrize("format", ["PNG", "JPEG", "GIF", "WEBP"])
def test_supported_static_image_formats_decode(format):
    assert (
        ingress._validate_image(image_bytes(format))
        == f"image/{'jpeg' if format == 'JPEG' else format.lower()}"
    )


def test_animated_image_is_explicitly_rejected():
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(
        output,
        format="GIF",
        save_all=True,
        append_images=[Image.new("RGB", (2, 2), "blue")],
    )
    with pytest.raises(ingress.InputError, match="animated images"):
        ingress._validate_image(output.getvalue())


@pytest.mark.asyncio
async def test_utf8_document_and_audio_text_are_explicit_inputs(monkeypatch):
    import utils.transcribe

    transcribe = AsyncMock(return_value="Spoken question")
    monkeypatch.setattr(utils.transcribe, "transcribe_voice", transcribe)
    service = resolver([Response(["\ufeff价格: 100".encode()]), Response([b"audio"])])
    refs = (
        ingress.MediaRef("text", "doc", "doc", None),
        ingress.MediaRef("audio", "audio", "audio", None),
    )
    resolved = await service.resolve(
        ingress.TelegramInput("Compare", refs, (1, 2)),
        language="zh",
        model_size="small",
    )
    assert (
        resolved.text
        == "Compare\n\nAttached text document:\n价格: 100\n\nSpoken question"
    )
    assert resolved.transcriptions == ("Spoken question",)
    transcribe.assert_awaited_once_with(b"audio", language="zh", model_size="small")


@pytest.mark.parametrize("payload", [b"\xff\xfe", b"hello\x00binary", b"\n\t "])
def test_non_utf8_binary_or_empty_document_is_refused(payload):
    with pytest.raises(ingress.InputError):
        ingress._decode_document(payload)


@pytest.mark.asyncio
async def test_combined_document_text_is_bounded(monkeypatch):
    monkeypatch.setattr(ingress, "MAX_TEXT_CHARS", 32)
    service = resolver([Response([b"a" * 25])])
    turn = ingress.extract_input(
        [
            message(
                document=Document(
                    file_id="doc", file_unique_id="doc", file_name="notes.txt"
                ),
                caption="Summarize",
            )
        ]
    )
    with pytest.raises(ingress.InputError) as failure:
        await service.resolve(turn)
    assert failure.value.code == "text_too_long"


@pytest.mark.asyncio
async def test_download_errors_hide_the_token():
    service = resolver([])
    service._bot.get_file.side_effect = RuntimeError(
        "request to https://api.telegram.org/botSECRET/getFile failed"
    )
    with pytest.raises(ingress.InputError) as failure:
        await service.resolve(ingress.extract_input([message(photo=[photo()])]))
    assert "SECRET" not in str(failure.value)
    assert failure.value.__suppress_context__
    response = Response([], status=404)
    service = resolver([response])
    with pytest.raises(ingress.InputError) as failure:
        await service.resolve(ingress.extract_input([message(photo=[photo()])]))
    assert "SECRET" not in str(failure.value)
    assert failure.value.__suppress_context__
    assert response.closed


@pytest.mark.asyncio
async def test_timeout_and_cancellation_release_download_slot(monkeypatch):
    monkeypatch.setattr(ingress, "DOWNLOAD_TIMEOUT_SECONDS", 0.01)
    service = resolver([])
    pending = asyncio.Event()

    async def wait_file(*args, **kwargs):
        await pending.wait()

    service._bot.get_file.side_effect = wait_file
    turn = ingress.extract_input([message(photo=[photo()])])
    with pytest.raises(ingress.InputError) as failure:
        await service.resolve(turn)
    assert failure.value.code == "download_failed"
    assert service._downloads._value == ingress.DOWNLOAD_CONCURRENCY
    task = asyncio.create_task(service.resolve(turn))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service._downloads._value == ingress.DOWNLOAD_CONCURRENCY


@pytest.mark.asyncio
async def test_shared_resolver_has_explicit_shutdown(monkeypatch):
    monkeypatch.setattr(ingress, "_resolver", None)
    bot = SimpleNamespace()
    service = ingress.get_resolver(bot)
    assert ingress.get_resolver(bot) is service
    with pytest.raises(ingress.InputError):
        ingress.get_resolver(SimpleNamespace())
    await ingress.close_resolver()
    assert service._closed
    assert ingress._resolver is service
    with pytest.raises(ingress.InputError, match="restarting"):
        ingress.get_resolver(bot)


@pytest.mark.asyncio
async def test_real_chunked_http_download_stops_at_limit(monkeypatch):
    from aiohttp import web

    monkeypatch.setattr(attachments, "MAX_BYTES", 8)

    async def serve(request):
        response = web.StreamResponse()
        await response.prepare(request)
        await response.write(b"12345")
        await response.write(b"67890")
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_get("/image", serve)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    server = TelegramAPIServer(
        base=f"http://127.0.0.1:{port}/bot{{token}}/{{method}}",
        file=f"http://127.0.0.1:{port}/{{path}}",
    )
    bot = SimpleNamespace(
        token="SECRET",
        session=AiohttpSession(api=server),
        get_file=AsyncMock(
            return_value=SimpleNamespace(file_size=None, file_path="image")
        ),
    )
    service = ingress.TelegramInputResolver(bot)
    try:
        with pytest.raises(ingress.InputError) as failure:
            await service.resolve(ingress.extract_input([message(photo=[photo()])]))
        assert failure.value.code == "file_too_large"
    finally:
        await service.close()
        await bot.session.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_global_download_concurrency_is_bounded():
    service = resolver([Response([image_bytes()]) for _ in range(6)])
    gate = asyncio.Event()
    full = asyncio.Event()
    active = 0
    peak = 0

    async def get_file(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == ingress.DOWNLOAD_CONCURRENCY:
            full.set()
        await gate.wait()
        active -= 1
        return SimpleNamespace(file_size=None, file_path="image")

    service._bot.get_file.side_effect = get_file
    turn = ingress.extract_input([message(photo=[photo()])])
    tasks = [asyncio.create_task(service.resolve(turn)) for _ in range(6)]
    try:
        await asyncio.wait_for(full.wait(), 1)
        assert peak == ingress.DOWNLOAD_CONCURRENCY
        gate.set()
        await asyncio.gather(*tasks)
        assert peak == ingress.DOWNLOAD_CONCURRENCY
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_stream_closes_response():
    started = asyncio.Event()

    class SlowResponse(Response):
        async def iter_chunked(self, size):
            started.set()
            await asyncio.Event().wait()
            yield b"unreachable"

    response = SlowResponse([])
    service = resolver([response])
    task = asyncio.create_task(
        service.resolve(ingress.extract_input([message(photo=[photo()])]))
    )
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert response.closed
    assert service._downloads._value == ingress.DOWNLOAD_CONCURRENCY


@pytest.mark.asyncio
async def test_image_validation_concurrency_remains_bounded_after_cancellation(
    monkeypatch,
):
    service = ingress.TelegramInputResolver(SimpleNamespace())
    loop = asyncio.get_running_loop()
    gate = threading.Event()
    full = asyncio.Event()
    lock = threading.Lock()
    active = 0
    peak = 0

    def validate(data):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == ingress.IMAGE_VALIDATION_CONCURRENCY:
                loop.call_soon_threadsafe(full.set)
        gate.wait(timeout=2)
        with lock:
            active -= 1
        return "image/png"

    monkeypatch.setattr(ingress, "_validate_image", validate)
    tasks = [asyncio.create_task(service._validate(b"image")) for _ in range(4)]
    try:
        await asyncio.wait_for(full.wait(), 1)
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        await asyncio.sleep(0)
        assert len(service._validation_workers) == 2
        assert service._validations._value == 0
        assert peak == ingress.IMAGE_VALIDATION_CONCURRENCY
        gate.set()
        await asyncio.gather(*tasks[1:])
        assert peak == ingress.IMAGE_VALIDATION_CONCURRENCY
    finally:
        gate.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await service.close()
    assert not service._validation_workers


@pytest.mark.asyncio
async def test_close_waits_for_decoder_after_its_request_is_cancelled(monkeypatch):
    service = ingress.TelegramInputResolver(SimpleNamespace())
    loop = asyncio.get_running_loop()
    gate = threading.Event()
    started = asyncio.Event()

    def validate(data):
        loop.call_soon_threadsafe(started.set)
        gate.wait(timeout=2)
        return "image/png"

    monkeypatch.setattr(ingress, "_validate_image", validate)
    request = asyncio.create_task(service._validate(b"image"))
    try:
        await asyncio.wait_for(started.wait(), 1)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        closing = asyncio.create_task(service.close())
        await asyncio.sleep(0)
        assert not closing.done()
        gate.set()
        await asyncio.wait_for(closing, 1)
        assert not service._validation_workers
    finally:
        gate.set()
        await asyncio.gather(request, return_exceptions=True)
        await service.close()
