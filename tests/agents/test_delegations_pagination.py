"""Task details expose every page instead of an unlabeled 3000-char preview."""

from types import SimpleNamespace

from condor.telegram.format import RICH_MAX_LEN
from handlers.delegations import _detail_text_and_keyboard


def test_every_result_page_is_reachable_without_repeating_the_internal_task():
    dt = SimpleNamespace(
        status="done",
        error="",
        result="开头\n\n" + "分析🪙 " * 16000 + "\n\n最终结论",
        agent_slug="fundamental_analyst",
        server_name="local",
        task_id="internal-task-id",
        task="internal instructions " * 1000,
    )
    texts = []
    page = 0
    while True:
        markdown, keyboard = _detail_text_and_keyboard(dt, 4, page)
        assert len(markdown.encode("utf-16-le")) // 2 <= RICH_MAX_LEN + 200
        assert (
            "internal instructions" not in markdown
            and "internal-task-id" not in markdown
        )
        texts.append(markdown.split("\n\n", 1)[1])
        buttons = [b for row in keyboard.inline_keyboard for b in row]
        next_buttons = [b for b in buttons if b.text == "Next ›"]
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
    markdown, _ = _detail_text_and_keyboard(dt, 0, 999)
    assert "Timed out" in markdown
    assert "Saved report; verification pending" in markdown
    assert "task incomplete" in markdown


def test_detail_keeps_markdown_tables():
    dt = SimpleNamespace(
        status="done",
        error="",
        result="| 币种 | 风险 |\n| --- | --- |\n| ABC | 高 |",
        agent_slug="scout",
    )
    markdown, _ = _detail_text_and_keyboard(dt, 0)
    assert "| 币种 | 风险 |" in markdown
    assert "| ABC | 高 |" in markdown
