"""A small MarkdownV2 checker for text the bot builds.

Telegram rejects a MarkdownV2 message with an unescaped reserved character or
an unbalanced entity; nothing offline does. This follows the rules in Telegram's
formatting documentation closely enough to catch those two mistakes: escapes,
``*``/``_``/``__``/``~``/``||`` toggles that must nest and close, inline code,
``pre`` blocks, links, and blockquote ``>`` at a line start.
"""

from __future__ import annotations

RESERVED = set("_*[]()~`>#+-=|{}.!")


def errors(text: str) -> list[str]:
    found: list[str] = []
    stack: list[str] = []
    i = 0
    n = len(text)

    def toggle(mark: str, at: int) -> None:
        if stack and stack[-1] == mark:
            stack.pop()
        elif mark in stack:
            found.append(f"crossing {mark!r} at {at}")
            while stack and stack.pop() != mark:
                pass
        else:
            stack.append(mark)

    while i < n:
        ch = text[i]
        if ch == "\\":
            if i + 1 >= n:
                found.append(f"dangling backslash at {i}")
            i += 2
            continue
        if ch == "`":
            fence = text.startswith("```", i)
            closer = "```" if fence else "`"
            j = i + len(closer)
            while j < n and not text.startswith(closer, j):
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "`" and not fence:
                    break
                j += 1
            if j >= n:
                found.append(f"unclosed code at {i}")
                return found
            i = j + len(closer)
            continue
        if ch == "*" or ch == "~":
            toggle(ch, i)
        elif ch == "_":
            if text.startswith("__", i):
                toggle("__", i)
                i += 2
                continue
            toggle("_", i)
        elif ch == "|":
            if text.startswith("||", i):
                toggle("||", i)
                i += 2
                continue
            found.append(f"unescaped '|' at {i}")
        elif ch == "[":
            stack.append("[")
        elif ch == "]":
            if "[" not in stack:
                found.append(f"unescaped ']' at {i}")
            else:
                while stack and stack.pop() != "[":
                    found.append(f"entity closed by link at {i}")
                if not text.startswith("(", i + 1):
                    found.append(f"link without url at {i}")
                else:
                    j = i + 2
                    while j < n and text[j] != ")":
                        j += 2 if text[j] == "\\" else 1
                    if j >= n:
                        found.append(f"unclosed link url at {i}")
                        return found
                    i = j
        elif ch == ">" and (i == 0 or text[i - 1] == "\n"):
            pass
        elif ch == "!" and text.startswith("![", i):
            pass
        elif ch in RESERVED:
            found.append(f"unescaped {ch!r} at {i}: {text[max(0, i - 15):i + 15]!r}")
        i += 1
    if stack:
        found.append(f"unclosed {stack!r}")
    return found


def assert_valid(text: str) -> None:
    problems = errors(text)
    assert not problems, f"{problems}\n---\n{text}"


def plain(text: str) -> str:
    """What a reader sees: escapes resolved and emphasis markers dropped."""
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
            continue
        if ch not in "*_~|`":
            out.append(ch)
        i += 1
    return "".join(out)
