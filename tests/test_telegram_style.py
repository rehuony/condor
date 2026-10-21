"""The shared Telegram visual grammar: valid MarkdownV2 and canonical labels."""

import ast
import re
from pathlib import Path

import pytest

from tests.support.markdown_v2 import assert_valid, errors
from utils import telegram_ui as ui
from utils.telegram_formatters import format_error_message


@pytest.mark.parametrize(
    "text",
    [
        ui.title("💼", "Portfolio (v2.0)", ui.server_meta("main-1", "online")),
        ui.screen(
            ui.section("Balances"),
            [
                ui.bullet("USDT", "1,234.50"),
                ui.bullet("Pair", "BTC-USDT", code_value=True),
            ],
            [ui.numbered(1, "binance_perpetual"), ui.numbered(2, "okx.spot!")],
            ui.hint("Tap a row to open it."),
        ),
        ui.notice("error", "Order failed", "Insufficient balance (need 10.5 USDT)."),
        ui.prompt("Enter amount", "Send the order size in USDT.", "BTC-USDT buy 0.01"),
        ui.STALE_BUTTON,
        format_error_message("bad_request: [x] {y} #1"),
        format_error_message("timeout", server_name="srv_a", server_status="offline"),
    ],
)
def test_kit_output_is_valid_markdown_v2(text):
    assert_valid(text)


def test_validator_rejects_unescaped_and_unbalanced_text():
    assert errors("Total: 1.5")
    assert errors("*bold")
    assert errors("*a _b* c_")
    assert not errors("*a _b_ c* `x.y` [link](https://e.com/a\\)b)")


def test_navigation_buttons_use_the_fixed_labels():
    assert ui.back_button("x:menu").text == "« Back"
    assert ui.back_button("x:menu", "Pools").text == "« Back to Pools"
    assert ui.close_button("x:close").text == "🚪 Close"
    assert ui.cancel_button("x:menu").text == "↩️ Cancel"
    row = ui.pager_row("2/5", "x:noop", prev_callback="x:p")
    assert [b.text for b in row] == ["‹ Prev", "2/5", " "]
    assert row[2].callback_data == "x:noop"


def test_stale_menu_detection():
    class Msg:
        def __init__(self, message_id):
            self.message_id = message_id

    data = {}
    assert ui.is_current_menu(data, "trade", Msg(1))
    ui.remember_menu(data, "trade", 2)
    assert not ui.is_current_menu(data, "trade", Msg(1))
    assert ui.is_current_menu(data, "trade", Msg(2))


ROOT = Path(__file__).resolve().parent.parent
#: Source trees whose strings reach Telegram users.
UI_SOURCES = ("handlers", "condor/telegram", "condor/trade_cards", "utils", "routines")
_CJK = re.compile(r"[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]")


def _string_constants():
    for top in UI_SOURCES:
        for path in sorted((ROOT / top).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    yield path.relative_to(ROOT), node.lineno, node.value


def test_user_facing_sources_are_english_only():
    mixed = [
        f"{path}:{line}"
        for path, line, text in _string_constants()
        if _CJK.search(text)
    ]
    assert not mixed, mixed


def _agent_and_entry_texts():
    from handlers.agents import _shared, openrouter
    from utils import auth

    model = type(
        "M", (), {"slug": "a/b-1.5", "prompt_price": 3, "completion_price": 15}
    )
    return [
        _shared.NO_SESSION,
        _shared.PRIVATE_ONLY,
        _shared.stale_binding_notice("funding_desk.v2"),
        openrouter._EXPIRED,
        openrouter._CANCELLED,
        ui.screen(*openrouter._model_rows(model)),
        auth.ACCESS_DENIED,
        ui.STALE_MENU,
        ui.SOMETHING_WRONG,
    ]


def test_agent_and_entry_notices_are_valid_markdown_v2():
    for text in _agent_and_entry_texts():
        assert_valid(text)


def _button_labels():
    for top in UI_SOURCES:
        for path in sorted((ROOT / top).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                func = getattr(node, "func", None)
                name = getattr(func, "id", getattr(func, "attr", ""))
                if not isinstance(node, ast.Call) or name != "InlineKeyboardButton":
                    continue
                for kw in node.keywords:
                    value = kw.value
                    if kw.arg != "text":
                        continue
                    if isinstance(value, ast.Constant):
                        yield path.relative_to(ROOT), node.lineno, value.value
                    elif isinstance(value, ast.JoinedStr) and value.values:
                        head = value.values[0]
                        if isinstance(head, ast.Constant):
                            yield path.relative_to(ROOT), node.lineno, head.value


_NAV_WORD = re.compile(r"^[^\w]*(Back|Close|Cancel|Refresh|Prev|Next)\b(.*)$")
_CANONICAL = {ui.BACK, ui.CLOSE, ui.CANCEL, ui.REFRESH, ui.PREV, ui.NEXT}


def test_navigation_buttons_everywhere_use_canonical_labels():
    wrong = []
    for path, line, label in _button_labels():
        match = _NAV_WORD.match(label)
        if not match:
            continue
        word, rest = match.groups()
        if word == "Back" and rest.startswith(" to "):
            ok = label.startswith(f"{ui.BACK} to ")
        elif rest.strip():
            continue  # a domain action such as "Cancel Order" or "Close Position"
        else:
            ok = label in _CANONICAL
        if not ok:
            wrong.append(f"{path}:{line} {label!r}")
    assert not wrong, wrong


def test_welcome_lists_commands_shortest_first_in_menu_wording():
    from handlers.commands import PUBLIC_COMMANDS
    from handlers.start import WELCOME_COMMANDS, _welcome_commands

    listed = _welcome_commands()
    names = [name for name, _ in listed]
    assert sorted(names) == sorted(WELCOME_COMMANDS)
    assert [len(name) for name in names] == sorted(len(name) for name in names)
    menu = dict(PUBLIC_COMMANDS)
    assert all(text == menu[name] for name, text in listed)


def test_running_tasks_rows_stay_flush_and_short(monkeypatch):
    """Telegram drops a leading indent on wrap, so rows carry none."""
    import asyncio
    from types import SimpleNamespace

    from handlers.routines import views

    sent = {}

    async def capture(update, text, markup):
        sent["text"] = text

    monkeypatch.setattr(views, "_edit_or_send", capture)
    monkeypatch.setattr(
        views,
        "_get_instances",
        lambda context: {
            "6b6753": {
                "status": "running",
                "routine_name": "trend_watch",
                "schedule": {"type": "continuous"},
                "run_count": 12,
                "last_result": "**Trend Watch** · " + "BTC-USDT " * 20 + "\nmore",
            }
        },
    )
    asyncio.run(views._show_tasks(SimpleNamespace(), SimpleNamespace()))

    text = sent["text"]
    assert_valid(text)
    assert all(not line.startswith(" ") for line in text.splitlines())
    last = next(line for line in text.splitlines() if line.startswith("• Last"))
    assert "**" not in last and "more" not in last and len(last) < 70
