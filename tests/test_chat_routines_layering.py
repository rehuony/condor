"""The chat agent stops writing into the tracked library (C6).

Every other assistant's authored content lands in the gitignored local root.
The chat's did not: its writable routines dir was the repo-root ``routines/``,
which git tracks and upstream actively maintains — 13 files, 48 commits touching
them over 60. So asking the chat agent to improve a shipped routine modified a
file upstream also modifies, and the next update touching it raised a
dirty-conflict whose escapes were lossy.

The carve-out that remains is load-bearing and these tests say so: FEAT-033
found that threading the slug through naively relocates the general *library*
and empties the catalog, silently, because a missing directory simply lists
nothing. Only the write target moves; the shipped root stays as the read
fallback.
"""

from __future__ import annotations

import pytest

from condor import paths
from condor.memory.paths import CHAT_SLUG
from routines.base import assistant_routines_dir, assistant_routines_dirs

# ── where writes land ──


@pytest.mark.parametrize("slug", [None, CHAT_SLUG])
def test_the_chat_writes_into_the_local_root(slug):
    """The whole point: `create_routine` can no longer touch the tracked tree."""
    target = assistant_routines_dir(slug)
    assert target == paths.local_agents_root() / CHAT_SLUG / "routines"
    # The thing that matters: it is not under the tracked tree any more.
    assert paths.stock_agents_root() not in target.parents
    assert not target.is_relative_to(paths._PROJECT_ROOT / "routines")


def test_a_specialist_is_unchanged():
    target = assistant_routines_dir("hyperliquid_expert")
    assert target == paths.local_agents_root() / "hyperliquid_expert" / "routines"


# ── and the catalog stays populated (the FEAT-033 regression) ──


@pytest.mark.parametrize("slug", [None, CHAT_SLUG])
def test_the_shipped_library_is_still_read(slug):
    """If this ever regresses, the catalog empties silently. Fail loudly instead."""
    dirs = assistant_routines_dirs(slug)
    assert len(dirs) == 2, "the chat lost a layer"
    assert dirs[0] == paths.local_agents_root() / CHAT_SLUG / "routines"

    shipped = dirs[1]
    assert shipped.name == "routines"
    assert shipped.is_dir(), "the shipped library is not where reads look"
    # The thing FEAT-033 broke: a populated catalog.
    assert list(shipped.glob("*.py")), "the shipped routine catalog reads as empty"


def test_local_shadows_shipped_not_the_other_way_round():
    dirs = assistant_routines_dirs(None)
    assert dirs.index(paths.local_agents_root() / CHAT_SLUG / "routines") == 0


# ── The chat's local layer is reported when upstream moves under it ──


def _chat_layers(tmp_path=None):
    from routines.base import assistant_routines_dirs

    return assistant_routines_dirs(None)


def test_an_improved_shipped_routine_is_reported_before_and_after(monkeypatch):
    """The scenario the local layer exists to create, and nothing reported it.

    FEAT-033 keeps the shipped library at the repo-root ``routines/`` and only
    the write target moved under the agent — so the generic scan looked for
    stock at ``<stock>/condor/routines``, found nothing, and filed the file as
    something the agent authored from scratch.
    """
    from condor.layering import all_stale_forks, locally_overridden

    local_dir, stock_dir = _chat_layers()
    shipped = stock_dir / "arb_check.py"
    original = shipped.read_bytes() if shipped.is_file() else None
    try:
        shipped.parent.mkdir(parents=True, exist_ok=True)
        shipped.write_text("# shipped\n", "utf-8")
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / "arb_check.py").write_text(
            "# shipped\n# my improvement\n", "utf-8"
        )

        # Before: the update is about to rewrite a file we have our own copy of.
        assert locally_overridden(["routines/arb_check.py"]) == [
            "routines/arb_check.py"
        ]

        # After: upstream's rewrite has landed and ours still wins.
        shipped.write_text("# UPSTREAM rewrote this\n", "utf-8")
        stale = [f for f in all_stale_forks() if f.rel == "routines/arb_check.py"]
        assert len(stale) == 1, "the chat's shadow of a shipped routine went unreported"
        assert stale[0].label == "condor/routines/arb_check.py"
        assert stale[0].unprovenanced is True, ".py can never carry a stamp"
    finally:
        if original is None:
            shipped.unlink(missing_ok=True)
        else:
            shipped.write_bytes(original)


def test_a_routine_the_chat_invented_is_not_reported():
    """No shipped counterpart means it was never a fork of anything."""
    from condor.layering import all_stale_forks

    local_dir, _ = _chat_layers()
    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "my_own_idea.py").write_text("# mine alone\n", "utf-8")

    assert [f for f in all_stale_forks() if f.rel.endswith("my_own_idea.py")] == []


def test_a_routine_the_chat_authors_is_discoverable(monkeypatch, tmp_path):
    """The catalog includes routines authored in the chat's writable local layer.

    `assistant_routines` must discover `<local>/condor/routines` alongside
    the root library and the shared one.
    """
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "local"))

    from routines.base import assistant_routines, assistant_routines_dir

    target = assistant_routines_dir(None)
    target.mkdir(parents=True, exist_ok=True)
    (target / "chat_authored.py").write_text(
        "from pydantic import BaseModel\n"
        "class Config(BaseModel):\n"
        '    """One the chat wrote"""\n'
        "async def run(config, context):\n"
        '    return "ok"\n',
        encoding="utf-8",
    )

    found = assistant_routines(None, force_reload=True)
    assert "chat_authored" in found, sorted(found)


def test_the_shipped_library_is_still_there_beside_it(monkeypatch, tmp_path):
    """FEAT-033's carve-out: the general catalog must not shrink."""
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "local"))

    from routines.base import assistant_routines

    found = assistant_routines(None, force_reload=True)
    assert len(found) > 5, f"the general library disappeared: {sorted(found)}"


def test_a_chat_authored_routine_keeps_the_general_librarys_source(
    monkeypatch, tmp_path
):
    """`agent_slug` stamps `source="agent:<slug>"`, and that is load-bearing.

    The MCP tool renames an `agent:` routine to `<slug>/<name>` and labels it
    agent-scoped with the specialist slug — empty for the chat. The chat's
    local layer is the writable half of the *general* library, so its
    routines carry the same bare name and `global` source as the rest of it.
    """
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "local"))

    from routines.base import assistant_routines, assistant_routines_dir

    target = assistant_routines_dir(None)
    target.mkdir(parents=True, exist_ok=True)
    (target / "authored.py").write_text(
        "from pydantic import BaseModel\n"
        "class Config(BaseModel):\n"
        '    """mine"""\n'
        "async def run(config, context):\n"
        '    return "ok"\n',
        encoding="utf-8",
    )

    found = assistant_routines(None, force_reload=True)
    assert found["authored"].source == "global", found["authored"].source


def test_an_authored_routine_can_actually_be_run(monkeypatch, tmp_path):
    """Listable is not runnable, and the catalog was only half the story.

    `RoutineStore._resolve_routine` starts a run through `get_routine`, which
    reads `discover_routines`. Adding the chat's local layer to
    `assistant_routines` alone put the routine in the catalog and left the
    resolver unable to find it, so starting it failed.
    """
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "local"))

    from routines.base import assistant_routines, assistant_routines_dir, get_routine

    target = assistant_routines_dir(None)
    target.mkdir(parents=True, exist_ok=True)
    (target / "brand_new.py").write_text(
        "from pydantic import BaseModel\n"
        "class Config(BaseModel):\n"
        '    """LOCAL-ONLY"""\n'
        "async def run(config, context):\n"
        '    return "ok"\n',
        encoding="utf-8",
    )

    assert "brand_new" in assistant_routines(None, force_reload=True)
    resolved = get_routine("brand_new")
    assert resolved is not None, "in the catalog but the resolver cannot find it"
    assert resolved.description == "LOCAL-ONLY"


def test_a_local_override_runs_instead_of_the_shipped_routine(monkeypatch, tmp_path):
    """Shadowing that the catalog shows but the resolver ignores is worse than
    no shadowing: the operator reads one description and runs other code."""
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "local"))

    from routines.base import (
        assistant_routines,
        assistant_routines_dir,
        get_routine,
        library_dir,
    )

    shipped = sorted(
        p.stem for p in library_dir().glob("*.py") if p.stem not in ("__init__", "base")
    )[0]
    target = assistant_routines_dir(None)
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{shipped}.py").write_text(
        "from pydantic import BaseModel\n"
        "class Config(BaseModel):\n"
        '    """LOCAL-OVERRIDE"""\n'
        "async def run(config, context):\n"
        '    return "ok"\n',
        encoding="utf-8",
    )

    catalog = assistant_routines(None, force_reload=True)
    assert catalog[shipped].description == "LOCAL-OVERRIDE"
    assert (
        get_routine(shipped).description == "LOCAL-OVERRIDE"
    ), "the catalog shows the override while the resolver runs the shipped one"


def test_a_removed_local_override_stops_shadowing(monkeypatch, tmp_path):
    """The mtime shortcut restores a root file by stem from the cache.

    Once the chat's local layer can shadow a root stem, that cached entry may
    be the override — so reusing it resurrects a local routine after its file
    is gone, and the shipped one stays unreachable.
    """
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "local"))

    from routines import base
    from routines.base import assistant_routines_dir, get_routine, library_dir

    monkeypatch.setattr(base, "_routines_cache", None)
    monkeypatch.setattr(base, "_root_routines_cache", None)

    shipped = sorted(
        p.stem for p in library_dir().glob("*.py") if p.stem not in ("__init__", "base")
    )[0]
    shipped_desc = get_routine(shipped).description

    target = assistant_routines_dir(None)
    target.mkdir(parents=True, exist_ok=True)
    override = target / f"{shipped}.py"
    override.write_text(
        "from pydantic import BaseModel\n"
        "class Config(BaseModel):\n"
        '    """LOCAL-OVERRIDE"""\n'
        "async def run(config, context):\n"
        '    return "ok"\n',
        encoding="utf-8",
    )
    assert get_routine(shipped).description == "LOCAL-OVERRIDE"

    override.unlink()
    assert (
        get_routine(shipped).description == shipped_desc
    ), "the deleted override is still being served from cache"
