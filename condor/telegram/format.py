"""Render complete Markdown answers within Telegram's message limits.

Rich messages split at :data:`RICH_MAX_LEN`. The HTML fallback still uses the
project Markdown parser, keeps Telegram-supported tags, and splits visible text
before escaping it so every page closes its formatting.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from urllib.parse import quote, urlsplit

import markdown
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

MAX_MESSAGE_LEN = 4096
RICH_MAX_LEN = 32_000


def split_text(text: str, max_len: int = MAX_MESSAGE_LEN) -> list[str]:
    """Split at paragraphs/lines without dropping text or splitting Unicode."""
    if max_len < 2:
        raise ValueError("max_len must allow a UTF-16 surrogate pair")
    chunks = []
    while text:
        size = 0
        end = 0
        for char in text:
            size += 2 if ord(char) > 0xFFFF else 1
            if size > max_len:
                break
            end += 1
        if end < len(text):
            for separator in ("\n\n", "\n", " "):
                boundary = text.rfind(separator, 0, end)
                if boundary > 0:
                    end = boundary + len(separator)
                    break
        chunks.append(text[:end])
        text = text[end:]
    return chunks


@dataclass(frozen=True)
class TelegramText:
    text: str
    html: str


def _research_url(href: str) -> str:
    """Telegram has no page origin to resolve dashboard research links against."""
    if not href.startswith("/research/"):
        return href

    from utils.config import WEB_URL

    try:
        base = urlsplit(WEB_URL)
    except ValueError:
        return href
    if base.scheme not in {"http", "https"} or not base.netloc:
        return href
    # Preserve existing percent escapes, queries and section anchors while
    # making filenames with spaces or Chinese characters valid URL targets.
    return WEB_URL.rstrip("/") + quote(href, safe="/%?=&#+:@")


def render_markdown(
    text: str,
    max_len: int = MAX_MESSAGE_LEN,
    *,
    literal_prefix: str = "",
    max_wire_len: int | None = None,
) -> list[TelegramText]:
    """Headings, lists, tables, code and links, balanced on every page."""
    html = markdown.markdown(text, extensions=["fenced_code", "tables", "sane_lists"])
    return render_html(
        html, max_len, literal_prefix=literal_prefix, max_wire_len=max_wire_len
    )


def render_html(
    text: str,
    max_len: int = MAX_MESSAGE_LEN,
    *,
    literal_prefix: str = "",
    max_wire_len: int | None = None,
) -> list[TelegramText]:
    """Split formatted HTML by visible text, closing tags on every page."""
    soup = BeautifulSoup(
        text, "html.parser", preserve_whitespace_tags={"[document]", "pre", "code"}
    )
    # A narrow chat cannot usefully display a wide table. Keep each value with
    # its column name instead of flattening away the relationship.
    for table in soup.find_all("table"):
        headers = [cell.get_text(" ", strip=True) for cell in table.select("thead th")]
        replacement = soup.new_tag("div")
        for row in table.select("tbody tr") or table.select("tr"):
            if headers and not row.find("td"):
                continue
            paragraph = soup.new_tag("p")
            for index, cell in enumerate(row.find_all(["td", "th"], recursive=False)):
                if index:
                    paragraph.append(soup.new_tag("br"))
                label = headers[index] if index < len(headers) else str(index + 1)
                paragraph.append(f"{label}: ")
                for child in list(cell.contents):
                    paragraph.append(child.extract())
            replacement.append(paragraph)
        table.replace_with(replacement)

    runs: list[tuple[str, tuple[tuple[str, str], ...]]] = []

    def visit(node, wrappers=()):
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            runs.append((str(node), wrappers))
            return
        if not isinstance(node, Tag):
            return
        name = node.name
        if name == "br":
            runs.append(("\n", ()))
            return
        if name == "hr":
            runs.append(("\n\n", ()))
            return
        if name == "img":
            runs.append((f"{node.get('alt', 'Image')} ({node.get('src', '')})", ()))
            return
        if name == "li":
            parent = node.parent
            marker = "• "
            if parent.name == "ol":
                try:
                    first = int(parent.get("start", 1))
                except ValueError:
                    first = 1
                marker = f"{first + len(node.find_previous_siblings('li'))}. "
            runs.append((marker, ()))
        tag = {
            "strong": "b",
            "em": "i",
            "del": "s",
            "strike": "s",
            "ins": "u",
            **{f"h{i}": "b" for i in range(1, 7)},
        }.get(name, name)
        child_wrappers = wrappers
        if name == "code" and node.has_attr("class"):
            language = escape(" ".join(node["class"]), quote=True)
            child_wrappers += ((f'<code class="{language}">', "</code>"),)
        elif tag in {"b", "i", "s", "u", "pre", "code", "tg-spoiler"}:
            child_wrappers += ((f"<{tag}>", f"</{tag}>"),)
        elif name == "span" and "tg-spoiler" in node.get("class", []):
            child_wrappers += (("<tg-spoiler>", "</tg-spoiler>"),)
        elif name == "blockquote":
            start = (
                "<blockquote expandable>"
                if node.has_attr("expandable")
                else "<blockquote>"
            )
            child_wrappers += ((start, "</blockquote>"),)
        elif name == "tg-emoji":
            emoji_id = escape(str(node.get("emoji-id", "")), quote=True)
            child_wrappers += ((f'<tg-emoji emoji-id="{emoji_id}">', "</tg-emoji>"),)
        elif name == "a":
            href = _research_url(str(node.get("href", "")))
            try:
                scheme = urlsplit(href).scheme
            except ValueError:
                scheme = ""
            if scheme in {"http", "https", "tg", "mailto"}:
                child_wrappers += ((f'<a href="{escape(href, quote=True)}">', "</a>"),)
            else:
                # Local artifact paths remain readable even without a public URL.
                for child in node.children:
                    visit(child, wrappers)
                runs.append((f" ({href})", wrappers))
                return
        for child in node.children:
            visit(child, child_wrappers)
        if (
            name in {"p", "div", "ul", "ol", "blockquote", "pre"}
            or name.startswith("h")
            and name[1:].isdigit()
        ):
            runs.append(("\n\n", ()))
        elif name == "li":
            runs.append(("\n", ()))

    for child in soup.children:
        visit(child)
    # Markdown's HTML serializer adds whitespace between blocks. Normalize only
    # those layout runs, never whitespace within code or an inline text node.
    normalized = []
    for value, wrappers in runs:
        if not wrappers and not value.strip() and "\n" in value:
            if normalized and not normalized[-1][1] and not normalized[-1][0].strip():
                normalized[-1] = ("\n\n", ())
            else:
                normalized.append((value, wrappers))
        else:
            normalized.append((value, wrappers))
    while normalized and not normalized[-1][0].strip():
        normalized.pop()
    if normalized and literal_prefix:
        normalized.insert(0, (literal_prefix + "\n\n", ()))
    return _pack_runs(normalized, max_len, max_wire_len)


def _pack_runs(runs, max_len, max_wire_len):
    """Split normalized runs without parsing/normalizing page boundaries again.

    A wire budget accounts for escaped characters and repeated HTML wrappers.
    Unsplittable markup (for example an oversized href) stays intact so the
    delivery layer can fall back to readable plain text with its destination.
    """
    plain = "".join(value for value, _ in runs)
    pages = []
    run_index = run_offset = 0
    for chunk in split_text(plain, max_len):
        remaining = len(chunk)
        parts = []
        page_runs = []
        while remaining:
            value, wrappers = runs[run_index]
            piece = value[run_offset : run_offset + remaining]
            page_runs.append((piece, wrappers))
            parts.append("".join(start for start, _ in wrappers))
            parts.append(escape(piece, quote=False))
            parts.append("".join(end for _, end in reversed(wrappers)))
            remaining -= len(piece)
            run_offset += len(piece)
            if run_offset == len(value):
                run_index += 1
                run_offset = 0
        html = "".join(parts)
        size = len(chunk.encode("utf-16-le")) // 2
        if (
            max_wire_len is not None
            and len(html.encode("utf-16-le")) // 2 > max_wire_len
            and size > 2
            and all(
                len("".join(start + end for start, end in wrappers).encode("utf-16-le"))
                // 2
                < max_wire_len - 2
                for _, wrappers in page_runs
            )
        ):
            pages.extend(_pack_runs(page_runs, max(2, size // 2), max_wire_len))
        else:
            pages.append(TelegramText(chunk, html))
    return pages


def plain_text(html: str) -> str:
    """Plain fallback that also retains the destinations of source links."""
    soup = BeautifulSoup(
        html, "html.parser", preserve_whitespace_tags={"[document]", "pre", "code"}
    )
    for link in soup.find_all("a"):
        link.replace_with(f"{link.get_text()} ({link.get('href', '')})")
    return soup.get_text()
