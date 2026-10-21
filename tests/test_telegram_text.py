"""Telegram preserves complete answers, their formatting and source links."""

import asyncio
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from condor.telegram.errors import bad_request, rate_limited
from condor.telegram.format import (
    MAX_MESSAGE_LEN,
    RICH_MAX_LEN,
    plain_text,
    render_html,
    render_markdown,
    render_rich_reply,
    split_text,
)
from condor.telegram.transport import send_markdown, send_text


class BalancedHTML(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.stack = []
        self.feed(text)
        assert not self.stack

    def handle_starttag(self, tag, attrs):
        assert tag in {"b", "i", "s", "u", "pre", "code", "a"}
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag


def test_long_chinese_report_keeps_tail_and_balanced_styles():
    source = "## 结论\n\n**值得观察🚀**\n\n" + "行情数据🪙 " * 1900
    source += "\n\n[官方依据](https://example.com/news?a=1&b=2)\n\n最后的风险条件。"
    pages = render_markdown(source)
    assert len(pages) > 2
    for page in pages:
        BalancedHTML(page.html)
        assert len(page.text.encode("utf-16-le")) // 2 <= MAX_MESSAGE_LEN
        assert (
            BeautifulSoup(
                page.html, "html.parser", preserve_whitespace_tags={"[document]"}
            ).get_text()
            == page.text
        )
    text = "".join(page.text for page in pages)
    assert text.count("行情数据🪙") == 1900
    assert text.endswith("最后的风险条件。")
    assert "<b>结论</b>" in pages[0].html
    assert "<b>值得观察🚀</b>" in pages[0].html
    assert 'href="https://example.com/news?a=1&amp;b=2"' in pages[-1].html


@pytest.mark.parametrize(
    "source", ["🪙" * 6000, "\n" + "x" * 9000, "一\n\n二\n" * 2000]
)
def test_plain_chunks_preserve_every_character(source):
    chunks = split_text(source)
    assert "".join(chunks) == source
    assert all(len(c.encode("utf-16-le")) // 2 <= MAX_MESSAGE_LEN for c in chunks)


def test_formatting_can_cross_a_page_boundary_without_broken_entities():
    source = (
        "**" + "长标题🚀" * 40 + "**\n\n```python\n" + "print('<a>')\n" * 30 + "```"
    )
    pages = render_markdown(source, max_len=100)
    for page in pages:
        BalancedHTML(page.html)
        assert len(page.text.encode("utf-16-le")) // 2 <= 100
    text = "".join(p.text for p in pages)
    assert text.count("长标题🚀") == 40
    assert text.count("print('<a>')") == 30


def test_tables_keep_column_names_values_and_links_on_a_narrow_screen():
    source = "| 币种 | 风险 |\n| --- | --- |\n| **ABC** | [解锁](https://example.com/unlock) |\n| DEF | 高 |"
    pages = render_markdown(source)
    assert len(pages) == 1
    text = pages[0].text
    assert "币种: ABC" in text and "风险: 解锁" in text
    assert "币种: DEF" in text and "风险: 高" in text
    assert "<b>ABC</b>" in pages[0].html
    assert "https://example.com/unlock" in pages[0].html
    assert "|" not in text


def test_code_and_literal_symbols_are_not_reinterpreted_as_markup():
    pages = render_markdown(
        "```text\n**literal** _value_ <script> & 中文\n```\n\n[文件](/tmp/report.md)"
    )
    assert "**literal** _value_ <script> & 中文" in pages[0].text
    assert "&lt;script&gt; &amp;" in pages[0].html
    assert "文件 (/tmp/report.md)" in pages[0].text


def test_rich_reply_normalizes_mixed_mobile_layout_without_losing_links():
    source = (
        "# Result\n\n1. Parent\n    - Child A\n    - Child B\n2. Next\n\n"
        "| Coin | Source |\n|---|---|\n| **BTC** | [Report](https://example.com) |"
    )
    html = render_rich_reply(source)
    assert html is not None
    assert html.startswith(
        "<b>Result</b><br/><br/>1. Parent<br/>\N{NO-BREAK SPACE}\N{NO-BREAK SPACE}• Child A"
        "<br/>\N{NO-BREAK SPACE}\N{NO-BREAK SPACE}• Child B<br/>2. Next"
    )
    assert "Coin: <b>BTC</b><br/>Source: " in html
    assert '<a href="https://example.com">Report</a>' in html
    assert "<table" not in html and "<h1" not in html
    assert render_markdown("- A\n- B")[0].text == "• A\n• B"


def test_rich_reply_keeps_inline_native_styles_outside_literal_or_escaped_content():
    source = (
        "~~removed~~ ||**hidden**|| ==marked==\n\n"
        r"\~\~literal\~\~ \|\|literal\|\| \=\=literal\=\="
        "\n\n`~~code~~ ||code|| ==code==`\n\n"
        "```text\n~~block~~ ||block|| ==block==\n```"
    )
    html = render_rich_reply(source)
    soup = BeautifulSoup(html, "html.parser")
    assert soup.s.get_text() == "removed"
    assert soup.find("tg-spoiler").b.get_text() == "hidden"
    assert soup.mark.get_text() == "marked"
    assert "~~literal~~ ||literal|| ==literal==" in plain_text(html)
    assert [node.get_text() for node in soup.find_all("code")] == [
        "~~code~~ ||code|| ==code==",
        "~~block~~ ||block|| ==block==\n",
    ]
    assert render_markdown("~~removed~~ ||secret|| ==marked==")[0].html == (
        "~~removed~~ ||secret|| ==marked=="
    )


def test_rich_html_inline_tags_and_explicit_math_survive_without_changing_legacy_html():
    source = r"<mark>hi</mark> <sub>i</sub> <sup>2</sup> <tg-math>x_i + y_j + \frac{a}{b}</tg-math>"
    assert render_rich_reply(source) == source
    assert render_html(source)[0].html == r"hi i 2 x_i + y_j + \frac{a}{b}"
    assert render_rich_reply("`<tg-math>x_i</tg-math>`") == (
        "<code>&lt;tg-math&gt;x_i&lt;/tg-math&gt;</code>"
    )


def test_plain_text_preserves_structural_breaks_and_link_destinations():
    assert plain_text(
        'First<br/><br/>Second<br><a href="https://example.com">Source</a>'
    ) == ("First\n\nSecond\nSource (https://example.com)")


@pytest.mark.parametrize("language,label", [("text", "Text"), ("python", "Python")])
def test_rich_reply_collapses_long_literal_blocks_without_reinterpreting_content(
    language, label
):
    literal = "  **literal** _value_ <script> & 中文😀 https://example.com\n" * 13
    html = render_rich_reply(f"Before\n\n```{language}\n{literal}```\n\nAfter")
    assert html is not None
    soup = BeautifulSoup(html, "html.parser")
    details = soup.find("details")
    assert details is not None and not details.has_attr("open")
    assert details.summary.get_text() == f"{label} · 13 lines"
    assert details.pre.get_text() == literal
    assert not details.pre.find("a") and not details.pre.find("b")
    assert "Before" in soup.get_text() and "After" in soup.get_text()
    # Notification/legacy HTML keeps its existing full-code presentation.
    assert "<details" not in render_markdown(f"```{language}\n{literal}```")[0].html


@pytest.mark.parametrize("language", ["text", "txt", "plain", "plaintext"])
def test_plain_code_labels_are_omitted_and_short_real_code_keeps_its_language(language):
    html = render_rich_reply(f"```{language}\n  <literal> & 😀\n```")
    soup = BeautifulSoup(html, "html.parser")
    assert soup.pre.get_text() == "  <literal> & 😀\n"
    assert not soup.code.has_attr("class") and soup.find("details") is None
    python = render_rich_reply("```python\nprint('ok')\n```")
    assert '<code class="language-python">' in python and "<details" not in python


def test_rich_reply_disclosure_counts_utf16_and_sanitizes_language_labels():
    literal = "😀" * 451 + "\n"
    html = render_rich_reply("```\n" + literal + "```")
    soup = BeautifulSoup(html, "html.parser")
    assert soup.summary.get_text() == "Text · 1 line"
    assert soup.pre.get_text() == literal
    unsafe = (
        '<pre><code class="language-private/label">' + "line\n" * 13 + "</code></pre>"
    )
    safe = BeautifulSoup(render_rich_reply(unsafe), "html.parser")
    assert safe.summary.get_text() == "Text · 13 lines"
    assert not safe.code.has_attr("class")


@pytest.mark.parametrize(
    "source",
    [
        "",
        "<!-- no visible content -->",
        "x" * (MAX_MESSAGE_LEN + 1),
        "| Long header name repeated | Value |\n|---|---|\n" + "|x|y|\n" * 200,
        "[Source](https://example.com/" + "x" * RICH_MAX_LEN + ")",
    ],
)
def test_rich_reply_rejects_empty_or_expanded_output_that_would_need_new_pages(source):
    assert render_rich_reply(source) is None


def test_rich_reply_parser_failure_returns_control_to_literal_delivery(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("Malformed input")

    monkeypatch.setattr("condor.telegram.format.render_markdown", fail)
    assert render_rich_reply("original **source**") is None


def test_a_malformed_link_cannot_prevent_delivery_of_the_answer():
    pages = render_markdown("[来源](https://[bad)\n\n仍需等待确认。")
    assert "仍需等待确认。" in "".join(p.text for p in pages)
    assert "https://[bad" in "".join(p.text for p in pages)


@pytest.mark.parametrize("renderer", [render_markdown, render_html])
@pytest.mark.parametrize(
    "base_url", ["https://dashboard.example.com", "https://dashboard.example.com:8443/"]
)
def test_research_links_use_the_dashboard_url(renderer, base_url, monkeypatch):
    monkeypatch.setattr("utils.config.WEB_URL", base_url)
    path = (
        "/research/perpetual-market-making-shadow/2026-10-10/040816-8f8ca72f/report.md"
    )
    title = "查看模拟实施与运行记录"
    source = (
        f"[{title}]({path})"
        if renderer is render_markdown
        else f'<a href="{path}">{title}</a>'
    )

    page = renderer(source)[0]

    assert page.text == title
    link = BeautifulSoup(page.html, "html.parser").find("a")
    assert link["href"] == base_url.rstrip("/") + path
    assert link.get_text() == title
    assert base_url.rstrip("/") + path in plain_text(page.html)


def test_research_links_encode_unicode_and_spaces_without_double_encoding(monkeypatch):
    monkeypatch.setattr("utils.config.WEB_URL", "https://dashboard.example.com")
    path = "/research/复盘/one%20two report.md?download=1&view=preview#结论"
    page = render_html(f'<a href="{path}">报告</a>')[0]
    link = BeautifulSoup(page.html, "html.parser").find("a")

    assert link["href"] == (
        "https://dashboard.example.com/research/%E5%A4%8D%E7%9B%98/one%20two%20report.md"
        "?download=1&view=preview#%E7%BB%93%E8%AE%BA"
    )
    assert "&amp;view=preview" in page.html


def test_research_link_resolution_leaves_external_links_and_code_unchanged(monkeypatch):
    monkeypatch.setattr("utils.config.WEB_URL", "https://dashboard.example.com")
    page = render_markdown(
        "[外部](https://other.example/research/report.md)\n\n"
        "`/research/topic/report.md`\n\n"
        "```text\n[示例](/research/topic/report.md)\n```\n\n"
        "[其他路径](/tmp/report.md)"
    )[0]
    links = BeautifulSoup(page.html, "html.parser").find_all("a")

    assert [link["href"] for link in links] == [
        "https://other.example/research/report.md"
    ]
    assert "<code>/research/topic/report.md</code>" in page.html
    assert "[示例](/research/topic/report.md)" in page.text
    assert "其他路径 (/tmp/report.md)" in page.text


def test_notification_plain_fallback_keeps_the_full_research_url(monkeypatch):
    monkeypatch.setattr("utils.config.WEB_URL", "https://dashboard.example.com")
    delivered = []

    async def send(**kwargs):
        if kwargs.get("parse_mode"):
            raise bad_request("Can't parse entities")
        delivered.append(kwargs["text"])
        return SimpleNamespace(message_id=len(delivered))

    asyncio.run(
        send_markdown(
            SimpleNamespace(send_message=send),
            42,
            "[查看报告](/research/topic/report.md)",
        )
    )
    assert delivered == [
        "查看报告 (https://dashboard.example.com/research/topic/report.md)"
    ]


def test_html_keeps_literal_markdown_and_telegram_formatting_across_pages():
    body = "**literal** _value_ & 中文🔎 " * 300
    html = (
        '<blockquote expandable><span class="tg-spoiler">'
        + body.replace("&", "&amp;")
        + '</span></blockquote><pre><code class="language-python">print(1)</code></pre>'
        + '<tg-emoji emoji-id="5368324170671202286">👍</tg-emoji>'
    )
    pages = render_html(html)
    assert len(pages) > 1
    assert "".join(p.text for p in pages).startswith(body)
    for page in pages:
        assert len(page.text.encode("utf-16-le")) // 2 <= MAX_MESSAGE_LEN
        assert page.html.count("<blockquote expandable>") == page.html.count(
            "</blockquote>"
        )
        assert page.html.count("<tg-spoiler>") == page.html.count("</tg-spoiler>")
    assert '<code class="language-python">print(1)</code>' in pages[-1].html
    assert '<tg-emoji emoji-id="5368324170671202286">👍</tg-emoji>' in pages[-1].html


def test_plain_fallback_keeps_sources_and_splits_expanded_urls():
    delivered = []
    url = "https://example.com/" + "q" * 4200

    async def send(**kwargs):
        if kwargs.get("parse_mode"):
            raise bad_request("Can't parse entities")
        delivered.append(kwargs["text"])
        return SimpleNamespace(message_id=len(delivered))

    asyncio.run(
        send_markdown(SimpleNamespace(send_message=send), 42, f"证据：[来源]({url})")
    )
    assert url in "".join(delivered)
    assert all(len(c) <= MAX_MESSAGE_LEN for c in delivered)


def test_rate_limit_retries_the_same_page_without_duplicating_the_rest(monkeypatch):
    delivered = []
    attempts = 0
    delays = []

    async def sleep(delay):
        delays.append(delay)

    async def send(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise rate_limited(2)
        delivered.append(kwargs["text"])
        return SimpleNamespace(message_id=attempts)

    monkeypatch.setattr("condor.telegram.transport.asyncio.sleep", sleep)
    source = "第一段\n\n" + "数据" * 4500 + "\n\n完整结尾"
    asyncio.run(send_markdown(SimpleNamespace(send_message=send), 42, source))
    assert "".join(plain_text(p) for p in delivered) == source
    assert delays == [2]


def test_missing_receipt_is_not_silently_reported_as_delivery():
    async def send(**kwargs):
        return None

    with pytest.raises(Exception, match="did not acknowledge"):
        asyncio.run(send_markdown(SimpleNamespace(send_message=send), 42, "result"))


def test_markdown_v2_is_passed_through_without_rewriting_escapes():
    delivered = []
    source = r"*bold* _italic_ escaped\_value \<tag\>"

    async def send(**kwargs):
        delivered.append(kwargs)
        return SimpleNamespace(message_id=len(delivered))

    asyncio.run(
        send_text(
            SimpleNamespace(send_message=send), 42, source, parse_mode="MarkdownV2"
        )
    )

    assert len(delivered) == 1
    assert delivered[0]["text"] == source
    assert delivered[0]["parse_mode"] == "MarkdownV2"


def test_markdown_uses_a_rich_message_and_keeps_tables():
    delivered = []

    async def send_rich(**kwargs):
        delivered.append(kwargs["rich_message"].markdown)
        return SimpleNamespace(message_id=len(delivered))

    source = "| 币种 | 风险 |\n| --- | --- |\n| **ABC** | [解锁](https://example.com/unlock) |"
    asyncio.run(send_markdown(SimpleNamespace(send_rich_message=send_rich), 42, source))
    assert delivered == [source]
    assert "|" in delivered[0]


def test_rich_markdown_splits_at_the_native_limit():
    delivered = []

    async def send_rich(**kwargs):
        delivered.append(kwargs["rich_message"].markdown)
        return SimpleNamespace(message_id=len(delivered))

    source = "段\n\n" + ("数据🪙\n" * 7000)
    asyncio.run(send_markdown(SimpleNamespace(send_rich_message=send_rich), 42, source))
    assert len(delivered) > 1
    assert "".join(delivered) == source
    assert all(
        len(chunk.encode("utf-16-le")) // 2 <= RICH_MAX_LEN for chunk in delivered
    )


def test_unparseable_rich_markdown_falls_back_to_html():
    delivered = []

    async def send_rich(**kwargs):
        raise bad_request("Can't parse entities")

    async def send(**kwargs):
        delivered.append(kwargs)
        return SimpleNamespace(message_id=len(delivered))

    asyncio.run(
        send_markdown(
            SimpleNamespace(send_rich_message=send_rich, send_message=send),
            42,
            "**ok**",
        )
    )
    assert delivered[0].get("parse_mode") == "HTML"
    assert "<b>ok</b>" in delivered[0]["text"]


def test_markdown_v2_split_entity_falls_back_per_page_without_losing_text():
    attempts = []
    delivered = []
    source = "*" + "中文🔎<literal> " * 1000 + "*"

    async def send(**kwargs):
        attempts.append(kwargs)
        if kwargs.get("parse_mode"):
            raise bad_request("Can't parse entities: unclosed bold entity")
        delivered.append(kwargs["text"])
        return SimpleNamespace(message_id=len(delivered))

    asyncio.run(
        send_text(
            SimpleNamespace(send_message=send), 42, source, parse_mode="MarkdownV2"
        )
    )

    assert len(delivered) > 1
    assert "".join(delivered) == source
    assert all(
        len(chunk.encode("utf-16-le")) // 2 <= MAX_MESSAGE_LEN for chunk in delivered
    )
    assert [call.get("parse_mode") for call in attempts] == ["MarkdownV2", None] * len(
        delivered
    )
