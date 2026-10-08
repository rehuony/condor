"""Telegram preserves complete answers, their formatting and source links."""

import asyncio
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup
from telegram.error import BadRequest, RetryAfter

from condor.telegram_text import (
    MAX_MESSAGE_LEN,
    plain_text,
    render_html,
    render_markdown,
    send_markdown,
    split_text,
)


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
            raise BadRequest("Can't parse entities")
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
            raise BadRequest("Can't parse entities")
        delivered.append(kwargs["text"])
        return SimpleNamespace(message_id=len(delivered))

    asyncio.run(
        send_markdown(SimpleNamespace(send_message=send), 42, f"证据：[来源]({url})")
    )
    assert url in "".join(delivered)
    assert all(len(c) <= MAX_MESSAGE_LEN for c in delivered)


@pytest.mark.parametrize("http", [False, True])
def test_rate_limit_retries_the_same_page_without_duplicating_the_rest(
    http, monkeypatch
):
    delivered = []
    attempts = 0
    delays = []

    async def sleep(delay):
        delays.append(delay)

    async def send(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            if http:
                return {"ok": False, "parameters": {"retry_after": 2}}
            raise RetryAfter(2)
        delivered.append(kwargs["text"])
        return {"ok": True, "result": {"message_id": attempts}}

    monkeypatch.setattr("condor.telegram_text.asyncio.sleep", sleep)
    source = "第一段\n\n" + "数据" * 4500 + "\n\n完整结尾"
    asyncio.run(send_markdown(SimpleNamespace(send_message=send), 42, source))
    assert "".join(plain_text(p) for p in delivered) == source
    assert delays == [2]


def test_http_failure_is_not_silently_reported_as_delivery():
    async def send(**kwargs):
        return {"ok": False, "description": "chat not found"}

    with pytest.raises(BadRequest, match="chat not found"):
        asyncio.run(send_markdown(SimpleNamespace(send_message=send), 42, "result"))


def test_http_bot_forwards_preview_and_keyboard_changes(monkeypatch):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    from condor.routine_store import _HttpBot

    calls = []

    async def post(method, data, files=None):
        calls.append((method, data))
        return {"ok": True}

    bot = _HttpBot()
    monkeypatch.setattr(bot, "_post", post)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Stop", callback_data="agent:cancel")]]
    )

    async def drive():
        await bot.send_message(chat_id=42, text="result", disable_web_page_preview=True)
        await bot.edit_message_text(
            chat_id=42, message_id=1, text="working", reply_markup=keyboard
        )
        await bot.edit_message_text(
            chat_id=42, message_id=1, text="done", reply_markup=None
        )
        await bot.delete_message(chat_id=42, message_id=2)

    asyncio.run(drive())
    assert calls[0][1]["disable_web_page_preview"] is True
    assert calls[1][1]["reply_markup"] == keyboard.to_dict()
    assert calls[2][1]["reply_markup"] == {"inline_keyboard": []}
    assert calls[3] == ("deleteMessage", {"chat_id": 42, "message_id": 2})
