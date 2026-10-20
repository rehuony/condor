"""Only ``condor/telegram`` and ``main.py`` may import the Telegram library.

Everything else goes through ``condor.telegram.types``, ``.errors`` and
``.transport`` so the library stays swappable behind that seam.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED = ["handlers", "condor", "routines", "agents", "utils", "mcp_servers"]
ALLOWED = {ROOT / "condor" / "telegram"}


def _library_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top == "aiogram":
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "aiogram":
                    found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


def test_only_the_seam_imports_the_telegram_library():
    offenders = []
    for folder in SCANNED:
        for path in sorted((ROOT / folder).rglob("*.py")):
            if any(parent in ALLOWED for parent in path.parents):
                continue
            offenders.extend(_library_imports(path))
    assert offenders == []


def test_types_module_exposes_what_handlers_need():
    from condor.telegram import types

    for name in (
        "Context",
        "Update",
        "InlineKeyboardButton",
        "InlineKeyboardMarkup",
        "LinkPreviewOptions",
        "NO_LINK_PREVIEW",
        "Message",
        "CallbackQuery",
        "BufferedInputFile",
    ):
        assert hasattr(types, name), name
    assert types.NO_LINK_PREVIEW.is_disabled is True
