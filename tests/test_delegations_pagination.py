"""Task details expose every page instead of an unlabeled 3000-char preview."""

from types import SimpleNamespace

from bs4 import BeautifulSoup

from handlers.delegations import _detail_text_and_keyboard


def test_every_result_page_is_reachable_without_repeating_the_internal_task():
    dt = SimpleNamespace(
        status="done",
        error="",
        result="开头\n\n" + "分析🪙 " * 2100 + "\n\n最终结论",
        agent_slug="fundamental_analyst",
        server_name="local",
        task_id="internal-task-id",
        task="internal instructions " * 1000,
    )
    texts = []
    page = 0
    while True:
        html, keyboard = _detail_text_and_keyboard(dt, 4, page)
        visible = BeautifulSoup(html, "html.parser").get_text()
        assert len(visible.encode("utf-16-le")) // 2 <= 4096
        assert (
            "internal instructions" not in visible and "internal-task-id" not in visible
        )
        texts.append(visible.split("\n\n", 1)[1])
        buttons = [b for row in keyboard.inline_keyboard for b in row]
        next_buttons = [b for b in buttons if b.text == "→"]
        if not next_buttons:
            break
        assert next_buttons[0].callback_data == f"deleg:view:4:{page + 1}"
        page += 1
        assert page < 20
    assert page > 1
    assert "".join(texts) == dt.result


def test_failed_task_detail_keeps_its_recoverable_progress():
    dt = SimpleNamespace(
        status="error",
        error="Timed out",
        result="Saved report; verification pending",
        agent_slug="scout",
    )
    html, _ = _detail_text_and_keyboard(dt, 0, 999)
    assert "Timed out" in html
    assert "Saved report; verification pending" in html
    assert "task incomplete" in html
