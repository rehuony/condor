"""Source continuity and presentation boundaries for Telegram messages."""

import pytest

from condor.telegram.output.presentation import (
    BODY_MAX_LEN,
    SegmentBuffer,
    final_payload,
    render_preview,
    tool_action,
    tool_inputs,
)


def utf16_size(text):
    return len(text.encode("utf-16-le")) // 2


def assert_contiguous(buffer):
    assert "".join(segment.text for segment in buffer.segments) == buffer.body
    position = 0
    for segment in buffer.segments:
        assert segment.start == position
        assert segment.text == buffer.body[segment.start : segment.end]
        assert utf16_size(segment.text) <= BODY_MAX_LEN
        position = segment.end
    assert position == len(buffer.body)


def test_appending_preserves_displayed_text_and_sealed_boundaries():
    buffer = SegmentBuffer()
    buffer.append("a" * 1000 + "\n\n" + "b" * 2200)
    visible = buffer.segments[-1].text
    buffer.append("c" * 1000)
    assert buffer.segments[0].text.startswith(visible)
    assert buffer.segments[0].sealed
    sealed = buffer.segments[0]
    buffer.append("\n\n" + "d" * 4000)
    assert buffer.segments[0] is sealed
    assert_contiguous(buffer)
    before = [(segment.start, segment.end, segment.text) for segment in buffer.segments]
    buffer.finalize()
    assert all(segment.sealed for segment in buffer.segments)
    assert before == [
        (segment.start, segment.end, segment.text) for segment in buffer.segments
    ]


@pytest.mark.parametrize("prefix", ["a" * 3499, "😀" * 1749 + "a", "😀" * 1750])
def test_unicode_boundary_never_splits_or_drops_text(prefix):
    buffer = SegmentBuffer()
    buffer.append(prefix)
    buffer.append("😀" * 1900 + "\n\nEnding")
    assert buffer.segments[0].text.startswith(prefix)
    assert_contiguous(buffer)


def test_new_segments_prefer_paragraph_then_line_boundaries():
    buffer = SegmentBuffer()
    buffer.append("a" * 2000 + "\n\n" + "b" * 1700 + "\n" + "c" * 2000)
    assert buffer.segments[0].text == "a" * 2000 + "\n\n"
    assert buffer.segments[1].text == "b" * 1700 + "\n"
    assert_contiguous(buffer)


def test_status_is_a_separate_nonempty_bounded_suffix():
    buffer = SegmentBuffer()
    buffer.append("😀" * 1750)
    raw = buffer.body
    preview = render_preview(buffer.segments[-1].text, "Reading…" * 500)
    assert preview.startswith(raw + "\n\n")
    assert utf16_size(preview) <= 4096
    assert buffer.body == raw
    assert render_preview("", "") == "Preparing response…"


@pytest.mark.parametrize(
    "text",
    [
        "```python\n" + "x = 1\n" * 1000 + "```",
        "| A | B |\n|---|---|\n" + "| x | y |\n" * 800,
    ],
)
def test_blocks_crossing_segments_remain_literal_without_repartitioning(text):
    buffer = SegmentBuffer()
    buffer.append(text)
    buffer.finalize()
    payloads = [final_payload(segment, buffer.body) for segment in buffer.segments]
    assert all(mode is None for _, mode in payloads)
    assert "".join(text for text, _ in payloads) == text


def test_complete_markdown_is_rich_but_unclosed_fence_remains_literal():
    buffer = SegmentBuffer()
    buffer.append(
        "# Result\n\n```python\nprint('ok')\n```\n\n| A | B |\n|---|---|\n| x | y |"
    )
    source, html = final_payload(buffer.segments[0], buffer.body)
    assert source == buffer.body
    assert "<b>Result</b>" in html
    assert "<pre>" in html and "A: x" in html and "B: y" in html
    incomplete = SegmentBuffer()
    incomplete.append("```python\nprint('ok')")
    assert final_payload(incomplete.segments[0], incomplete.body) == (
        incomplete.body,
        None,
    )


@pytest.mark.parametrize(
    ("title", "kind", "identifier", "expected"),
    [
        ("Read /private/key", "read", "", "Reading…"),
        ("curl https://example.test?secret=x", "execute", "", "Running…"),
        ("token=secret", "other", "", "Using tool…"),
        ("mcp__condor__get_prices", "fetch", "", "Fetching prices"),
        ("arbitrary private description", "read", "read_file", "Reading…"),
    ],
)
def test_tool_labels_only_include_identifiers(title, kind, identifier, expected):
    assert tool_action(title, kind, identifier) == expected


@pytest.mark.parametrize(
    ("name", "kind", "arguments", "expected"),
    [
        (
            "Read",
            "read",
            {"file_path": "/private/project/input.py"},
            "Reading input.py",
        ),
        ("Write", "write", {"path": r"C:\private\input.py"}, "Writing input.py"),
        (
            "manage_skill",
            "mcp",
            {"action": "read_file", "file": "notes/SKILL.md"},
            "Reading SKILL.md",
        ),
        (
            "mcp__mcp-hummingbot__get_prices",
            "mcp",
            {"trading_pairs": ["BTC-USDT"]},
            "Fetching BTC-USDT prices",
        ),
        (
            "get_prices",
            "mcp",
            {"trading_pairs": ["BTC-USDT", "ETH-USDT"], "connector_name": "binance"},
            "Fetching binance prices",
        ),
        (
            "get_market_data",
            "mcp",
            {"action": "candles", "trading_pair": "SOL-USDT"},
            "Fetching SOL-USDT candles",
        ),
        (
            "manage_servers",
            "mcp",
            {"action": "status", "name": "local-server"},
            "Checking local-server",
        ),
        ("manage_servers", "mcp", {"action": "list"}, "Checking Hummingbot API"),
        (
            "manage_servers",
            "read",
            {"action": "delete", "name": "SECRET"},
            "Using tool…",
        ),
        (
            "explore_geckoterminal",
            "mcp",
            {"token_address": "SECRET"},
            "Fetching GeckoTerminal",
        ),
        (
            "WebFetch",
            "fetch",
            {"url": "https://user:SECRET@example.com/private?token=SECRET#SECRET"},
            "Fetching example.com",
        ),
        (
            "run_code",
            "mcp",
            {"label": "SECRET", "code": "print('SECRET')"},
            "Running code",
        ),
        ("run_code", "execute", {"action": "get"}, "Reading code history"),
        ("run_code", "execute", {"action": "delete"}, "Using tool…"),
        ("run_code", "execute", {"action": ["run"]}, "Using tool…"),
        ("unknown", "other", {"name": "SECRET", "path": "/SECRET"}, "Using tool…"),
    ],
)
def test_current_action_only_exposes_recognized_short_targets(
    name, kind, arguments, expected
):
    assert tool_action(identifier=name, kind=kind, arguments=arguments) == expected


@pytest.mark.parametrize(
    ("kind", "arguments"),
    [
        ("read", {"path": "/private/input.py\nSECRET"}),
        ("read", {"file_path": "private/\u202eSECRET.py"}),
        ("read", {"file_path": "x" * 80 + "\nSECRET.py"}),
        ("fetch", {"url": "https://example.com\nSECRET"}),
        ("fetch", {"url": "https://[broken"}),
        ("fetch", {"url": "file:///private/SECRET"}),
    ],
)
def test_malformed_targets_fall_back_without_echoing_them(kind, arguments):
    assert (
        tool_action(kind=kind, arguments=arguments)
        == {"read": "Reading…", "fetch": "Fetching…"}[kind]
    )


def test_pending_is_not_execution_and_only_status_fields_are_retained():
    arguments = tool_inputs(
        '{"file_path":"/private/input.py","code":"SECRET","output":"SECRET"}'
    )
    assert arguments == {"file_path": "/private/input.py"}
    assert (
        tool_action(identifier="Read", arguments=arguments, status="pending")
        == "Preparing to read input.py…"
    )
    assert (
        tool_inputs({"path": "", "file": None, "trading_pairs": [], "name": ""}) == {}
    )
    assert tool_inputs("{incomplete") == {}


@pytest.mark.parametrize(
    ("filename", "short"),
    [
        ("long_" + "a" * 80 + ".py", "long_" + "a" * 58 + "…"),
        ("😀" * 40 + ".py", "😀" * 31 + "…"),
    ],
)
def test_long_filenames_keep_a_unicode_safe_short_target(filename, short):
    arguments = {"path": "/private/" + filename}
    assert tool_action(identifier="Read", arguments=arguments) == "Reading " + short
    assert utf16_size(short) <= 64
    assert (
        tool_action(identifier="Read", arguments=arguments, status="pending")
        == "Preparing to read " + short
    )


@pytest.mark.parametrize("text", ["<!-- literal answer -->", "---"])
def test_visually_empty_markdown_stays_literal(text):
    buffer = SegmentBuffer()
    buffer.append(text)
    buffer.finalize()
    assert final_payload(buffer.segments[0], buffer.body) == (text, None)
