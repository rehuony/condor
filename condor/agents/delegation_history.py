"""Agent runs that outlived the process that ran them (FEAT-035, FEAT-058).

The live registry in :mod:`condor.agents.delegate` is in-memory and per-process
by design: membership there means "this can still be running". A restart
therefore used to erase every trace of delegated work from the dashboard, even
though the work itself was on disk the whole time.

This module is the read side of those files, and nothing else: no state, no
writes, no knowledge of the web layer. It rebuilds a delegation record from what
was persisted, so the same routes and the same two React components that serve a
live task can serve one from two weeks ago.

**The owner is the first path segment** (FEAT-051). A record lives at
``.condor/users/{user_id}/delegations/{task_id}/``, beside that person's
conversations, which is why every reader here takes a ``user_id`` first:
answering "what did this user delegate" is opening one directory, and reading
someone else's is not a check a caller could forget to make -- it is a path they
cannot name. ``user_id=None`` means "every user" and is reachable only from an
admin path or the boot reconciler.

Since FEAT-058 a record is one of two *kinds*. A **delegation** is the shape
described below, in full. A **consult** -- what ``delegate(action="ask")`` writes
-- is the same ``status.json`` and nothing else: it returns its answer straight to
the caller, so there is no transcript and no event stream to persist, and its
record carries a ``caller`` (the agent that asked) where a delegation carries a
tool count. A record written before that field existed has no ``kind``, and it was
a delegation -- so the default here is a fact about the past, not a guess.

Each ``{task_id}/`` directory holds ``status.json`` (the record),
``events.json`` (the wire transcript) and ``transcript.md``. A complete status
record needs no transcript read. If a status or events write failed, the
transcript in that same owner directory is still readable. Listings hydrate
only the requested page, and unchanged transcript parses are cached.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Collection, Iterator
from pathlib import Path
from typing import Any

from condor import paths
from condor.agents.run_records import DEFAULT_KIND, KIND_CONSULT

log = logging.getLogger(__name__)

# The current shape: one directory per delegation, the same idiom conversations
# already use. ``status.json`` is ``registry_file``'s own default name.
DELEGATION_STATUS_FILENAME = "status.json"
DELEGATION_TRANSCRIPT_FILENAME = "transcript.md"
DELEGATION_EVENTS_FILENAME = "events.json"

# A record whose state was never recorded. Honest: the file exists, so the task
# does; what it ended as we do not know.
UNKNOWN = "unknown"


def _safe(value: int | str) -> str | None:
    """One path segment, or None. Task ids reach this module from a URL path."""
    try:
        return paths.safe_id(value)
    except paths.UnsafeIdError:
        return None


def _mtime(path: Path) -> float:
    """A file's mtime, or 0.0 when it cannot be stat'd. Never raises."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _owners(user_id: int | str | None) -> list[str]:
    """Whose delegations to look in. ``None`` is the cross-user seam."""
    if user_id is not None:
        safe = _safe(user_id)
        return [safe] if safe else []
    return list(paths.iter_user_ids())


# ── markdown fallback ──────────────────────────────────────────────────────
# The header emitted by ``delegate._persist_transcript`` is fixed-format, so the
# three lines below are the only prose ever parsed. A miss degrades the field to
# empty (or the status to "unknown"); it never raises.

_HEADER_FIELD = "- **{label}:** "


def _header_value(text: str, label: str) -> str:
    prefix = _HEADER_FIELD.format(label=label)
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
        if line.startswith("## "):  # past the header block
            break
    return ""


def _section(text: str, heading: str) -> str:
    """The body of a ``## heading`` section, up to the next ``## ``."""
    marker = f"\n## {heading}\n"
    start = text.find(marker)
    if start < 0:
        return ""
    body = text[start + len(marker) :]
    nxt = body.find("\n## ")
    return (body if nxt < 0 else body[:nxt]).strip()


def _read_markdown(md_path: Path) -> dict[str, Any]:
    """The fields one transcript carries, independent of who is asking.

    Everything here comes from the file itself, so two callers asking about the
    same path get the same answer -- which is what makes the parse cacheable.
    """
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        log.debug("Unreadable delegation transcript at %s", md_path, exc_info=True)
        text = ""

    status = (_header_value(text, "Status") or UNKNOWN).lower()
    is_error = status == "error"
    try:
        tool_count = int(_header_value(text, "Tool calls") or 0)
    except ValueError:
        tool_count = 0

    # The transcript is written when the task ends, so its mtime is the only
    # timestamp these records have. Reported as the start, since that is what
    # orders the list -- and it is closer to the truth than zero.
    mtime = _mtime(md_path)

    body = _section(text, "Error" if is_error else "Result")
    if body == "(none)":
        body = ""

    # "-" is the placeholder ``_persist_transcript`` renders for "no server",
    # not a server name: reading it back as one would put a dash on the wire.
    server = _header_value(text, "Server")

    return {
        "agent": _header_value(text, "Agent"),
        "user_id": 0,
        "chat_id": 0,
        "server_name": None if server in ("", "-") else server,
        "task": _section(text, "Task"),
        "status": status,
        "result": "" if is_error else body,
        "error": body if is_error else "",
        "conversation_id": "",
        "on_complete": "",
        "started_at": mtime,
        "ended_at": mtime,
        "tool_count": tool_count,
    }


# Memoized on ``(mtime, size)``, the idiom ``sessions_index`` already uses for
# snapshots. A transcript embeds every tool call's input and output, so one file
# runs to hundreds of KB, and the dashboard re-lists while a task is running --
# parsing an unchanged file once per process is the whole point. A file that is
# rewritten gets a new mtime or size and re-parses, so a delegation that changes
# is never served from a stale parse.
_MD_CACHE: dict[Path, tuple[float, int, dict[str, Any]]] = {}

# Incomplete status records can still need their transcript. Bound the cache;
# every entry is rebuildable from the file it names.
_MD_CACHE_MAX = 512


def _parse_markdown(md_path: Path) -> dict[str, Any]:
    """:func:`_read_markdown`, parsed at most once per version of the file."""
    try:
        stat = md_path.stat()
        key: tuple[float, int] | None = (stat.st_mtime, stat.st_size)
    except OSError:
        key = None

    cached = _MD_CACHE.get(md_path)
    if key is not None and cached is not None and cached[:2] == key:
        return cached[2]

    parsed = _read_markdown(md_path)
    if key is not None:
        if len(_MD_CACHE) >= _MD_CACHE_MAX:
            _MD_CACHE.clear()
        _MD_CACHE[md_path] = (key[0], key[1], parsed)
    return parsed


def _from_markdown(md_path: Path, agent_slug: str, task_id: str) -> dict[str, Any]:
    """A record rebuilt from the transcript alone.

    Always a delegation: a consult writes a status record without a transcript.
    """
    parsed = _parse_markdown(md_path)
    return {
        "task_id": task_id,
        "agent": parsed["agent"] or agent_slug,
        "kind": DEFAULT_KIND,
        "caller": "",
        **{k: v for k, v in parsed.items() if k != "agent"},
    }


# ── status.json ────────────────────────────────────────────────────────────


# What ``delegate._record_delegation_status`` writes. Their *presence* is what
# says "this status file is the whole record" and no backfill is owed. Presence,
# not truthiness: a running record's ``result`` is legitimately "" and an errored
# one's is too, so an emptiness test would re-read the transcript on exactly the
# rows the dashboard polls every five seconds.
_CONTENT_KEYS = frozenset({"state", "task", "result", "error", "tool_count"})


def _needs_markdown(data: dict[str, Any]) -> bool:
    """Whether this status file is missing content only the transcript has.

    A consult never has one -- it returns its answer straight to the caller and
    writes a ledger entry, not a tape (FEAT-058) -- so it is answered by kind
    rather than by the field set it deliberately does not carry.
    """
    if data.get("kind") == KIND_CONSULT:
        return False
    return not _CONTENT_KEYS <= data.keys()


def _status_sort_key(data: dict[str, Any], md_path: Path) -> float:
    """The ``started_at`` :func:`_from_status` will report, without parsing.

    Mirrors that function's fallback chain exactly -- transcript mtime included,
    since that is the only timestamp a status file missing ``started_at`` has. A
    ``stat()`` answers it; the file is not opened.
    """
    return data.get("started_at") or _mtime(md_path) or data.get("updated_at") or 0.0


def _from_status(
    status_path: Path,
    agent_slug: str,
    task_id: str,
    md_path: Path,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A record rebuilt from its status file, backfilled from the markdown.

    An incomplete status write can leave the task or result only in the
    transcript. Complete status records avoid that read entirely; see
    :data:`_CONTENT_KEYS`.

    ``data`` lets a caller that has already read the status file hand it over
    rather than paying for a second read.
    """
    from condor.runtime.registry_file import is_stale, read_status

    if data is None:
        data = read_status(status_path.parent, status_path.name) or {}
    md = (
        _from_markdown(md_path, agent_slug, task_id)
        if _needs_markdown(data) and md_path.is_file()
        else {}
    )

    # A `running` record stamped by a boot that is not ours belongs to a process
    # that died without recording an end -- FEAT-012's distinction, and the only
    # honest label for it.
    status = data.get("state") or md.get("status") or UNKNOWN
    if is_stale(data):
        status = "interrupted"

    def pick(key: str, fallback: Any = "") -> Any:
        value = data.get(key)
        if value in (None, ""):
            value = md.get(key, fallback)
        return fallback if value in (None, "") else value

    stamp: float | None = None

    def transcript_mtime() -> float:
        """The transcript's mtime, stat'd at most once and only if needed."""
        nonlocal stamp
        if stamp is None:
            stamp = _mtime(md_path)
        return stamp

    return {
        "task_id": task_id,
        "agent": data.get("agent_slug") or md.get("agent") or agent_slug,
        # A record written before FEAT-058 carries no kind, and it was a
        # delegation -- because that is the only thing that wrote one. So the
        # default is not a guess, and the whole back-compatibility story is this
        # line.
        "kind": data.get("kind") or DEFAULT_KIND,
        # Who asked. Only a consult records it (the delegate path has no such
        # notion yet), so "" means "not recorded", never "nobody".
        "caller": data.get("caller") or "",
        "user_id": data.get("user_id") or md.get("user_id") or 0,
        "chat_id": data.get("chat_id") or md.get("chat_id") or 0,
        "server_name": data.get("server_name") or md.get("server_name") or None,
        "task": pick("task"),
        "status": status,
        "result": pick("result"),
        "error": pick("error"),
        "conversation_id": data.get("conversation_id") or "",
        "on_complete": data.get("on_complete") or "",
        # The transcript's mtime stands in for a timestamp the status file does
        # not carry -- a status write that never landed, on either end. It is a
        # ``stat()``, not a parse, so it costs the same whether the transcript
        # was read or skipped, and the answer is the one the old backfill gave.
        "started_at": data.get("started_at")
        or transcript_mtime()
        or data.get("updated_at")
        or 0.0,
        "ended_at": data.get("ended_at") or transcript_mtime() or 0.0,
        "tool_count": data.get("tool_count") or md.get("tool_count") or 0,
    }


# ── the current shape: one directory per delegation ─────────────────────────


def _from_dir(record_dir: Path) -> dict[str, Any] | None:
    """The record in one ``{task_id}/`` directory, or None if there is none."""
    task_id = record_dir.name
    status_path = record_dir / DELEGATION_STATUS_FILENAME
    md_path = record_dir / DELEGATION_TRANSCRIPT_FILENAME
    if status_path.is_file():
        return _from_status(status_path, "", task_id, md_path)
    if md_path.is_file():
        return _from_markdown(md_path, "", task_id)
    return None


# ── listing: sort first, hydrate the survivors ──────────────────────────────
# A listing walks every delegation an owner still has on disk -- bounded since
# PERF-222, but always more than a page -- and returns at most ``limit`` of
# them. So the walk yields
# *handles*: the sort key, the id (for de-duplication), and a thunk that builds
# the record. Only the rows that survive the sort are ever built.

Entry = tuple[float, str, Callable[[], dict[str, Any] | None]]


def _entry_from_dir(record_dir: Path) -> Entry | None:
    """A sortable handle on one ``{task_id}/`` directory, hydrated on demand.

    The status file is read here rather than in the thunk: it is small, and the
    sort key is in it. It is handed to the thunk so the row that does get built
    does not read it twice.
    """
    from condor.runtime.registry_file import read_status

    task_id = record_dir.name
    status_path = record_dir / DELEGATION_STATUS_FILENAME
    md_path = record_dir / DELEGATION_TRANSCRIPT_FILENAME
    if status_path.is_file():
        data = read_status(record_dir, DELEGATION_STATUS_FILENAME) or {}
        return (
            _status_sort_key(data, md_path),
            task_id,
            lambda: _from_status(status_path, "", task_id, md_path, data),
        )
    if md_path.is_file():
        return _mtime(md_path), task_id, lambda: _from_markdown(md_path, "", task_id)
    return None


def _entries_of(user_id: int | str) -> Iterator[Entry]:
    """Every delegation this user has on disk, unhydrated."""
    try:
        children = sorted(paths.delegations_dir(user_id).iterdir())
    except (OSError, paths.UnsafeIdError):
        return
    for child in children:
        if not child.is_dir():
            continue
        entry = _entry_from_dir(child)
        if entry is not None:
            yield entry


# ── retention: which directories are safe to consider ──────────────────────


def terminal_record_dirs(
    user_id: int | str, states: Collection[str], kind: str | None = None
) -> list[tuple[float, str, Path]]:
    """This owner's records whose status file reports one of ``states``, oldest first.

    The read half of PERF-222's retention sweep, and nothing more: it answers
    *which directories are candidates*, as ``(sort key, task_id, directory)``.
    Deciding and deleting stay on the write side
    (:func:`condor.agents.delegate.prune_delegation_records`), which is the only
    thing that knows which task ids are still live in this process -- so this
    module keeps its one job, reading.

    A directory with no status file, or one whose state is not in ``states``, is
    left out entirely: it is neither evictable nor counted towards a cap. Sorted
    by the key :func:`list_history` orders on, so the first record here is the
    row that listing would have shown last.

    ``kind`` narrows the candidates to one channel, which is how retention keeps
    a per-kind cap (FEAT-058). It costs nothing: the status dict is already
    parsed here to read the state.
    """
    from condor.runtime.registry_file import read_status

    try:
        # Materialised inside the guard on purpose: ``iterdir`` is a generator,
        # so a missing (or unreadable) directory raises on the first *step*, not
        # on the call -- the guard was catching nothing and the walk raised
        # through its caller.
        children = list(paths.delegations_dir(user_id).iterdir())
    except (OSError, paths.UnsafeIdError):
        return []

    records: list[tuple[float, str, Path]] = []
    for child in children:
        try:
            if not child.is_dir() or not (child / DELEGATION_STATUS_FILENAME).is_file():
                continue
        except OSError:  # pragma: no cover - a directory vanishing mid-walk
            continue
        data = read_status(child, DELEGATION_STATUS_FILENAME) or {}
        if data.get("state") not in states:
            continue
        if kind is not None and (data.get("kind") or DEFAULT_KIND) != kind:
            continue
        key = _status_sort_key(data, child / DELEGATION_TRANSCRIPT_FILENAME)
        records.append((key, child.name, child))
    records.sort(key=lambda record: (record[0], record[1]))
    return records


# ── the read API ───────────────────────────────────────────────────────────


def list_history(
    *,
    user_id: int | str | None = None,
    agent_slug: str | None = None,
    kind: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Recorded agent runs, newest first.

    Scoped to one person unless ``user_id`` is None, which is admin-only.
    Filtering by agent is a filter, not a lookup: the store is keyed by user,
    and the agent-first view reads the same status files. ``kind`` is the
    same shape of filter: ``None`` is every channel (the Activity tab), a kind
    is one of them (the chat dock, which is about background tasks only).

    Reads only what is on disk -- the caller merges in the live registry, which
    is the authority for anything still running in this process.

    Ordering happens before hydration: every delegation on disk is *ordered*,
    only ``limit`` of them are *built* (PERF-204). What bounds the *ordering* is
    retention: :func:`condor.agents.delegate.prune_delegation_records` caps how
    many terminal records an owner keeps, so the walk no longer grows for the
    life of the install (PERF-222).
    """
    entries: list[Entry] = []
    for owner in _owners(user_id):
        try:
            entries.extend(_entries_of(owner))
        except OSError:
            log.debug("Could not list delegations for user %s", owner, exc_info=True)

    entries.sort(key=lambda e: e[0], reverse=True)

    records: list[dict[str, Any]] = []
    cap = max(0, limit)
    for _key, _task_id, load in entries:
        if len(records) >= cap:
            break
        record = load()
        if record is None:
            continue
        if agent_slug and record.get("agent") != agent_slug:
            continue
        if kind and (record.get("kind") or DEFAULT_KIND) != kind:
            continue
        records.append(record)
    return records


def read_history(user_id: int | str | None, task_id: str) -> dict[str, Any] | None:
    """One recorded delegation, or None if nothing on disk describes it.

    With an owner this is a single ``is_dir()``: the caller's id is a path
    segment, so a stranger's task is not merely refused, it is unnameable.
    """
    safe = _safe(task_id)
    if safe is None:
        return None

    for owner in _owners(user_id):
        record_dir = paths.delegation_dir(owner, safe)
        if record_dir.is_dir():
            record = _from_dir(record_dir)
            if record is not None:
                return record

    return None


def read_history_events(
    user_id: int | str | None, task_id: str
) -> tuple[list[dict[str, Any]], str]:
    """``(events, markdown)`` for a recorded delegation.

    The sidecar is preferred: it is the same projection
    :func:`condor.agents.delegate.events_for_wire` serves for a live task, so a
    finished delegation renders identically to a running one. Records written
    before the sidecar existed return no events and their transcript instead --
    the client renders that markdown rather than showing an empty transcript.
    """
    safe = _safe(task_id)
    if safe is None:
        return [], ""

    for owner in _owners(user_id):
        record_dir = paths.delegation_dir(owner, safe)
        if not record_dir.is_dir():
            continue
        found = _events_or_markdown(
            record_dir / DELEGATION_EVENTS_FILENAME,
            record_dir / DELEGATION_TRANSCRIPT_FILENAME,
        )
        if found is not None:
            return found

    return [], ""


def _events_or_markdown(
    sidecar: Path, md_path: Path
) -> tuple[list[dict[str, Any]], str] | None:
    """The transcript from one record, or None when neither file is there."""
    if sidecar.is_file():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            events = data.get("events") if isinstance(data, dict) else None
            if isinstance(events, list):
                return events, ""
        except (OSError, json.JSONDecodeError):
            log.debug("Unreadable events sidecar at %s", sidecar, exc_info=True)

    if md_path.is_file():
        try:
            return [], md_path.read_text(encoding="utf-8")
        except OSError:
            log.debug("Unreadable transcript at %s", md_path, exc_info=True)
            return [], ""
    return None
