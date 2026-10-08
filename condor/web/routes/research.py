"""Authenticated preview and download of this install's research files."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from condor import paths
from condor.web.auth import get_current_user, require_admin
from condor.web.models import WebUser

router = APIRouter(prefix="/research", tags=["research"])

MAX_TEXT_PREVIEW = 1024 * 1024
MAX_IMAGE_PREVIEW = 10 * 1024 * 1024
_TEXT_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".ndjson",
    ".yaml",
    ".yml",
    ".log",
    ".patch",
    ".diff",
    ".html",
    ".htm",
}
_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.S)


def _markdown_preview(content: str, filename: str) -> dict:
    """Separate the reading surface from the original, downloadable Markdown.

    Use PyYAML, already used by the other document stores, with whole-line
    delimiters so a title containing `---` is not mistaken for the closing fence.
    Invalid or non-mapping frontmatter stays visible instead of losing text.
    """
    body = content
    meta = {}
    match = _FRONTMATTER.match(content)
    if match and len(match.group(1)) <= 16 * 1024:
        try:
            loaded = yaml.safe_load(match.group(1))
            if isinstance(loaded, dict):
                meta = loaded
                body = content[match.end() :].lstrip()
        except (yaml.YAMLError, RecursionError):
            pass

    title = meta.get("title")
    title = title.strip()[:300] if isinstance(title, str) else ""
    summary = meta.get("summary")
    summary = summary.strip()[:1000] if isinstance(summary, str) else ""
    heading = re.match(r"\A\s*#\s+([^\n]+)(?:\n|$)", body)
    if heading:
        heading_title = heading.group(1).strip()
        if not title or title == heading_title:
            title = title or heading_title
            body = body[heading.end() :].lstrip()

    created_at = meta.get("created_at")
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            created_at = None
    created_at = (
        created_at.isoformat()
        if isinstance(created_at, datetime) and created_at.tzinfo
        else None
    )
    return {
        "title": title or filename,
        "summary": summary,
        "body": body,
        "created_at": created_at,
    }


def _resolve(path: str) -> Path:
    # URL decoding is owned by Starlette. Never decode again here: a literal
    # percent in a filename is not a second path separator or traversal token.
    parts = path.split("/")
    if any(not part or part.startswith(".") for part in parts) or any(
        ord(char) < 32 or char == "\\" for char in path
    ):
        raise HTTPException(404, "Research file not found")
    try:
        root = paths.research_dir().resolve()
        candidate = (root / path).resolve(strict=True)
        if (
            candidate.is_relative_to(root)
            and not any(
                part.startswith(".") for part in candidate.relative_to(root).parts
            )
            and candidate.is_file()
        ):
            return candidate
    except (OSError, ValueError, RuntimeError):
        pass
    raise HTTPException(404, "Research file not found")


@router.get("/{path:path}")
def get_research_file(
    path: str,
    download: bool = False,
    user: WebUser = Depends(get_current_user),
):
    # These files predate per-user attribution and live in one shared directory.
    # Match ownerless reports: an authenticated trader must not gain access to
    # another user's research simply by knowing its name.
    require_admin(user)
    file = _resolve(path)
    try:
        stat = file.stat()
        suffix = file.suffix.lower()
        if download:
            return FileResponse(
                file,
                filename=Path(path).name,
                media_type=_IMAGE_TYPES.get(suffix, "application/octet-stream"),
                headers=_HEADERS,
                stat_result=stat,
            )

        preview = "none"
        content = None
        if suffix in _TEXT_EXTENSIONS and stat.st_size <= MAX_TEXT_PREVIEW:
            # Bound the read as well as checking stat: a writer may still be
            # appending. Large or non-UTF-8 files remain downloadable intact.
            with file.open("rb") as stream:
                data = stream.read(MAX_TEXT_PREVIEW + 1)
            if len(data) <= MAX_TEXT_PREVIEW:
                try:
                    content = data.decode("utf-8-sig")
                    preview = "markdown" if suffix in {".md", ".markdown"} else "text"
                except UnicodeDecodeError:
                    pass
        elif suffix in _IMAGE_TYPES and stat.st_size <= MAX_IMAGE_PREVIEW:
            preview = "image"

        return JSONResponse(
            {
                "path": path,
                "name": Path(path).name,
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc
                ).isoformat(),
                "preview": preview,
                "content": content,
                **(
                    _markdown_preview(content, Path(path).name)
                    if preview == "markdown"
                    else {
                        "title": Path(path).name,
                        "summary": "",
                        "body": content,
                        "created_at": None,
                    }
                ),
            },
            headers=_HEADERS,
        )
    except OSError:
        raise HTTPException(404, "Research file not found") from None
