"""What can be updated, how it is versioned, and what stands in the way.

Components are described as data rather than as branches in a handler. There is
one: Condor itself, a git checkout. hummingbot-api is not one of them -- it is
built from source and deployed on its own host with its own ``make check-update``
/ ``make update`` / ``make deploy``, and the dashboard only reports what it runs
(Settings → Hummingbot API). An updater here would pull an image the API no
longer publishes and recreate containers its ``make deploy`` is meant to build.

Nothing here talks to Telegram or to the dashboard. It shells out through
:mod:`utils.updater` and returns dataclasses with ``to_wire()``, so a surface
serializes them without a translation layer.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any

from utils import updater

log = logging.getLogger(__name__)

# Component keys. Public: a surface addresses a component by these.
CONDOR = "condor"

# How long a status check is reused. The hourly notice and an admin refreshing
# the screen twice must not each cost a fetch.
CHECK_TTL = 60.0

# Commit subjects shown before collapsing into "and N more".
_DETAIL_LINES = 5


# ── Wire shapes ──


@dataclass(frozen=True)
class Facet:
    """One answer to "what version is this", for one way of being versioned."""

    kind: str  # "repo"
    current: str
    available: str | None = None
    behind: int = 0
    up_to_date: bool = True
    detail: list[str] = field(default_factory=list)
    error: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComponentStatus:
    """Everything a surface needs to render one component's card."""

    key: str
    name: str
    facets: dict[str, Facet]
    up_to_date: bool = True

    def to_wire(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "facets": {k: v.to_wire() for k, v in self.facets.items()},
            "up_to_date": self.up_to_date,
        }


@dataclass(frozen=True)
class Block:
    """Something that stops the update, in words the admin can act on."""

    component: str
    code: str
    message: str
    paths: list[str] = field(default_factory=list)
    resolutions: list[str] = field(default_factory=list)

    def to_wire(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Warning:
    """A consequence worth knowing about. Never a reason to refuse."""

    component: str
    code: str
    message: str

    def to_wire(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Preflight:
    """The answer to "can this run, and what will it do"."""

    components: list[str]
    blocks: list[Block] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blocks

    def to_wire(self) -> dict[str, Any]:
        return {
            "components": list(self.components),
            "blocks": [b.to_wire() for b in self.blocks],
            "warnings": [w.to_wire() for w in self.warnings],
            "steps": list(self.steps),
            "ok": self.ok,
        }


# ── Descriptors ──


@dataclass(frozen=True)
class Component:
    """A thing that can be updated. A table entry, not a subclass."""

    key: str
    name: str
    repo_dir: str


def _table() -> dict[str, Component]:
    """The component table, read at call time so the env can still move it."""
    return {CONDOR: Component(CONDOR, "Condor", updater.CONDOR_DIR)}


def get(key: str) -> Component | None:
    return _table().get(key)


def keys() -> list[str]:
    """Component keys that exist on this install, in apply order."""
    return [k for k, c in _table().items() if os.path.isdir(c.repo_dir)]


# ── Facets ──


async def _repo_facet(repo_dir: str) -> Facet:
    """HEAD versus ``origin/<branch>``, having fetched first."""
    if not await updater.is_git_repo(repo_dir):
        return Facet(
            kind="repo",
            current="unknown",
            up_to_date=False,
            error=f"{repo_dir} is not a git checkout.",
        )

    info = await updater.check_for_updates(repo_dir=repo_dir)
    branch = info.get("branch") or "?"
    local = info.get("local_commit") or "?"

    if info.get("error"):
        return Facet(
            kind="repo",
            current=f"{branch}@{local}",
            up_to_date=False,
            error=str(info["error"]),
        )

    behind = int(info.get("commits_behind") or 0)
    detail: list[str] = []
    if behind:
        lines = [ln for ln in (info.get("commit_log") or "").split("\n") if ln.strip()]
        detail = lines[:_DETAIL_LINES]
        if behind > len(detail):
            detail.append(f"…and {behind - len(detail)} more")

    return Facet(
        kind="repo",
        current=f"{branch}@{local}",
        available=f"{branch}@{info['remote_commit']}" if behind else None,
        behind=behind,
        up_to_date=behind == 0,
        detail=detail,
    )


async def status(key: str) -> ComponentStatus:
    """Every facet of one component."""
    component = _table()[key]
    repo = await _repo_facet(component.repo_dir)
    return ComponentStatus(
        key=component.key,
        name=component.name,
        facets={"repo": repo},
        up_to_date=repo.up_to_date,
    )


# ── The cached check ──

_cache: tuple[float, list[ComponentStatus]] | None = None
_cache_lock = asyncio.Lock()


async def check(*, force: bool = False) -> list[ComponentStatus]:
    """Status of every component on this install, at most once per minute."""
    global _cache

    async with _cache_lock:
        if not force and _cache and (time.monotonic() - _cache[0]) < CHECK_TTL:
            return _cache[1]

        statuses = await asyncio.gather(*(status(k) for k in keys()))
        result = list(statuses)
        _cache = (time.monotonic(), result)
        return result


def invalidate() -> None:
    """Drop the cached check. Called after anything that changes a version."""
    global _cache
    _cache = None


# ── Preflight ──


def is_forkable(component_key: str, rel_path: str) -> bool:
    """Whether a conflicting path can be kept by moving it to the local root.

    Only Condor's shipped agent library. ``agents/<slug>/...`` has a local
    counterpart that reads shadow per item, so moving an edit there is exactly
    what the product would have done with it. Anything else -- ``main.py``, the
    Makefile -- has no such layer, and "keeping"
    it by moving it somewhere nothing reads would be a quieter way of losing it
    than discarding.
    """
    if component_key != CONDOR:
        return False
    parts = PurePosixPath(rel_path).parts
    return len(parts) > 2 and parts[0] == "agents" and not parts[1].startswith("_")


def _resolutions_for(component_key: str, paths: list[str]) -> list[str]:
    """The ways out of a conflict, with the non-lossy one offered where it works.

    ``keep-mine`` appears only when every conflicting path is forkable. Offering
    it on a mixed set would be worse than not offering it: it reads as "keep all
    of this", and the paths it could not move would be reset anyway.
    """
    base = ["discard", "stash", "cancel"]
    if paths and all(is_forkable(component_key, p) for p in paths):
        return ["keep-mine", *base]
    return base


def _incoming_unknown(component: Component, branch: str, because: str) -> Block:
    """The block for "we could not work out what this update would bring in".

    Distinct from ``dirty-conflict``, which names files: this one names no
    paths because the point is that the path list does not exist. Cancel is the
    only resolution -- there is nothing here for the admin to discard or stash,
    only a comparison to retry once the checkout or the network is healthy.
    """
    return Block(
        component=component.key,
        code="incoming-unknown",
        message=(
            f"Could not compare this checkout against origin/{branch} "
            f"({because}), so there is no telling whether the incoming commits "
            "would overwrite local work. Refusing to fast-forward blind."
        ),
        resolutions=["cancel"],
    )


async def repo_blocks(component: Component) -> list[Block]:
    """What would stop ``git merge --ff-only`` in this checkout.

    Not "is the tree dirty" — that question blocks forever on a checkout which
    is also a runtime working directory, as Condor's is. The question is whether the
    incoming commits would clobber something local, which is the intersection
    of the two sets and is almost always empty.
    """
    if not await updater.is_git_repo(component.repo_dir):
        return [
            Block(
                component=component.key,
                code="not-a-repo",
                message=(
                    f"{component.repo_dir} is not a git checkout, so there is "
                    "nothing to fast-forward."
                ),
                resolutions=["cancel"],
            )
        ]

    # Before anything reads a branch name: a detached checkout has none, but
    # ``rev-parse --abbrev-ref HEAD`` answers the literal string ``HEAD``, and
    # everything downstream then treats that as a branch. ``origin/HEAD``
    # resolves to the remote's default branch, so the comparison silently
    # becomes "how far is this commit behind main", nothing reports being
    # ahead, and ``merge --ff-only origin/HEAD`` succeeds -- fast-forwarding a
    # detached checkout onto main and leaving it detached, with no message
    # saying so. Refuse instead, and say which commit it is sitting on.
    if await updater.is_detached(component.repo_dir):
        sha = await updater.get_local_commit(component.repo_dir)
        return [
            Block(
                component=component.key,
                code="detached-head",
                message=(
                    f"HEAD is detached at {sha} in {component.repo_dir}. "
                    "Check out a branch before updating — otherwise the update "
                    "would fast-forward onto the remote's default branch and "
                    "leave the checkout detached."
                ),
                resolutions=["cancel"],
            )
        ]

    branch = await updater.get_current_branch(component.repo_dir)

    # Fetch first, always. The incoming set is only meaningful against an
    # ``origin/`` that is current, and this runs immediately before the
    # fast-forward that would act on it -- judging the blockers from a stale
    # ref is how local work gets clobbered by commits nobody had seen yet. So a
    # fetch that failed is not a detail to shrug off: it is the whole basis of
    # the comparison below, gone.
    fetched, fetch_output = await updater.fetch(component.repo_dir)
    if not fetched:
        if updater.is_missing_remote_ref(fetch_output):
            # Not an unreachable remote: the remote answered and has no branch
            # of this name. Saying "unreachable" sent the reader to check a
            # network that was never the problem, and `incoming-unknown` is the
            # wrong shape too -- nothing is incoming, there is no upstream.
            default = await updater.remote_default_branch(component.repo_dir)
            switch = f" (`git switch {default}`)" if default else ""
            return [
                Block(
                    component=component.key,
                    code="no-upstream-branch",
                    message=(
                        f"`{branch}` exists only in this checkout — origin has "
                        "no branch of that name, so there is nothing to update "
                        "from. Push it to set an upstream, or switch to a "
                        f"branch that tracks origin{switch}."
                    ),
                    resolutions=["cancel"],
                )
            ]
        return [_incoming_unknown(component, branch, "the remote is unreachable")]

    ahead, dirty, incoming = await asyncio.gather(
        updater.ahead_count(component.repo_dir, branch),
        updater.dirty_state(component.repo_dir),
        updater.incoming_paths(component.repo_dir, branch),
    )

    if ahead:
        return [
            Block(
                component=component.key,
                code="diverged",
                message=(
                    f"This checkout has {ahead} commit{'s' if ahead != 1 else ''} "
                    f"that {branch} on the remote does not, so it cannot be "
                    "fast-forwarded. Push or reset it by hand."
                ),
                resolutions=["cancel"],
            )
        ]

    # None is not "nothing incoming" -- it is "the diff did not resolve", and
    # the two must not land in the same branch. Falling through here with an
    # empty set would report zero blockers for an update nobody can vouch for.
    if incoming is None:
        return [_incoming_unknown(component, branch, f"origin/{branch} is unreadable")]

    incoming_set = set(incoming)
    if not incoming_set:
        return []

    blocks: list[Block] = []
    tracked = sorted(incoming_set.intersection(dirty.tracked))
    untracked = sorted(incoming_set.intersection(dirty.untracked))

    if tracked:
        blocks.append(
            Block(
                component=component.key,
                code="dirty-conflict",
                message=(
                    f"{len(tracked)} file{'s' if len(tracked) != 1 else ''} "
                    "changed locally would be overwritten by the incoming "
                    "commits."
                ),
                paths=tracked,
                resolutions=_resolutions_for(component.key, tracked),
            )
        )
    if untracked:
        blocks.append(
            Block(
                component=component.key,
                code="untracked-conflict",
                message=(
                    f"{len(untracked)} local file{'s' if len(untracked) != 1 else ''} "
                    "not in git would be overwritten by the incoming commits."
                ),
                paths=untracked,
                resolutions=_resolutions_for(component.key, untracked),
            )
        )
    return blocks


def _steps_for(component_key: str) -> list[str]:
    """The plan for one component, in the order it will run.

    Must stay in step with ``run._plan``: the confirm screen and the progress
    screen are meant to be the same list.
    """
    return [
        "Fast-forward the Condor checkout",
        "Restore any work stashed to clear the way",
        "Sync dependencies",
        "Rebuild the dashboard if the update touched it",
        "Check the install over",
        "Relaunch Condor to apply it",
    ]


_STALE_FORK_CAP = 8


# An update can write a rebuilt venv, a fresh node_modules and a new bundle.
# Running out part-way is the worst input to everything else here,
# and nothing checked: shutil.disk_usage appeared nowhere in the update path.
_DISK_FLOOR_GB = 3.0


def _locally_overridden(incoming: list[str]) -> list[str]:
    """:func:`condor.layering.locally_overridden`, but never fatal to a preflight."""
    from condor.layering import locally_overridden

    try:
        return locally_overridden(incoming)
    except OSError:
        log.debug("Could not check for locally overridden paths", exc_info=True)
        return []


def _incoming_warnings(incoming: list[str]) -> list[Warning]:
    """Consequences that follow from *which files* an update would write."""
    out: list[Warning] = []
    if any(p.startswith("frontend/") for p in incoming):
        # frontend_needs_build() is consulted inside the run and never in the
        # preflight, so nothing told the person reading the dashboard that the
        # page they are on is the thing about to be replaced.
        out.append(
            Warning(
                component=CONDOR,
                code="frontend-will-rebuild",
                message=(
                    "This update rebuilds the dashboard. The new bundle is built "
                    "alongside the running one and swapped in, so the page you "
                    "are reading keeps working — reload it once the update "
                    "finishes to pick up the new one."
                ),
            )
        )
    overridden = _locally_overridden(incoming)
    if overridden:
        # The predictive half. stale_forks() compares a fork against the stock
        # file on disk, which only moves once the fast-forward has landed -- so
        # on its own it reports the divergence *after* the update that caused
        # it. This says so while there is still a decision to make.
        shown = ", ".join(overridden[:_STALE_FORK_CAP])
        if len(overridden) > _STALE_FORK_CAP:
            shown += f", and {len(overridden) - _STALE_FORK_CAP} more"
        out.append(
            Warning(
                component=CONDOR,
                code="update-changes-overridden-files",
                message=(
                    f"This update changes {len(overridden)} shipped "
                    f"file{'s' if len(overridden) != 1 else ''} that you have "
                    f"your own version of ({shown}). Your versions will keep "
                    "being used, so these changes will not reach your agents."
                ),
            )
        )
    if "condor/migrations.py" in incoming:
        # startup() migrates this deployment's data on every boot and the moves
        # are one-way, so restoring old *code* onto migrated *data* leaves
        # Condor looking for directories that no longer exist. FEAT-115 widened
        # that to the agent tree, so a rollback now also strands forked
        # playbooks, skills and mutes.
        out.append(
            Warning(
                component=CONDOR,
                code="migration-incoming",
                message=(
                    "This update migrates stored data, and the moves are "
                    "one-way. Rolling back to the current commit afterwards "
                    "will not restore the old layout."
                ),
            )
        )
    return out


def _disk_warning(repo_dir: str) -> Warning | None:
    """Warn when there is not obviously room for what the update is about to write."""
    try:
        usage = shutil.disk_usage(repo_dir)
    except OSError:
        log.debug("Could not read free space for %s", repo_dir, exc_info=True)
        return None
    free_gb = usage.free / (1024**3)
    if free_gb >= _DISK_FLOOR_GB:
        return None
    return Warning(
        component=CONDOR,
        code="low-disk",
        message=(
            f"Only {free_gb:.1f} GB free where Condor is installed. An update "
            f"writes a synced virtualenv, a fresh node_modules and a new "
            f"dashboard bundle — running out part-way through leaves the "
            f"install half-built. Free some space first."
        ),
    )


def _stale_fork_warning() -> Warning | None:
    """Local versions of shipped files that this install will keep using.

    Non-blocking on purpose. Owning a customization is what FEAT-115 shipped;
    what never shipped is the notice, so the divergence compounded silently on
    files carrying trading rules. This is that notice.

    The path list is capped -- an install that has forked thirty playbooks needs
    a number and a few examples, not thirty lines in a Telegram message.
    """
    from condor.layering import all_stale_forks

    try:
        stale = all_stale_forks()
    except OSError:
        log.debug("Could not check for stale forks", exc_info=True)
        return None
    if not stale:
        return None

    moved = [f for f in stale if not f.retired and not f.unprovenanced]
    shadowing = [f for f in stale if not f.retired and f.unprovenanced]
    retired = [f for f in stale if f.retired]

    parts: list[str] = []
    if shadowing:
        # Separated because the advice differs: a stamped fork was deliberately
        # taken from a known version, while these never recorded one -- a
        # routine's .py cannot hold a stamp, and a file the agent created under
        # a name upstream later shipped never had one to take. Either way the
        # local copy wins and upstream's is unreachable.
        shown = ", ".join(f.label for f in shadowing[:_STALE_FORK_CAP])
        if len(shadowing) > _STALE_FORK_CAP:
            shown += f", and {len(shadowing) - _STALE_FORK_CAP} more"
        parts.append(
            f"{len(shadowing)} local file{'s' if len(shadowing) != 1 else ''} "
            f"shadow{'' if len(shadowing) != 1 else 's'} a shipped one of the "
            f"same name and differ{'' if len(shadowing) != 1 else 's'} from it "
            f"({shown}). Yours are used; upstream's are not reachable. Rename "
            "yours if you meant to keep both."
        )
    if moved:
        shown = ", ".join(f.label for f in moved[:_STALE_FORK_CAP])
        if len(moved) > _STALE_FORK_CAP:
            shown += f", and {len(moved) - _STALE_FORK_CAP} more"
        parts.append(
            f"You have local versions of {len(moved)} shipped "
            f"file{'s' if len(moved) != 1 else ''} that upstream has since "
            f"changed ({shown}). Your versions stay in use, so those changes "
            "will not reach your agents."
        )
    if retired:
        shown = ", ".join(f.label for f in retired[:_STALE_FORK_CAP])
        if len(retired) > _STALE_FORK_CAP:
            shown += f", and {len(retired) - _STALE_FORK_CAP} more"
        parts.append(
            f"{len(retired)} local file{'s' if len(retired) != 1 else ''} "
            f"no longer exist{'' if len(retired) != 1 else 's'} upstream "
            f"({shown}) and will keep running. Mute a retired playbook rather "
            "than deleting it."
        )
    return Warning(component=CONDOR, code="stale-forks", message=" ".join(parts))


async def preflight(component_keys: list[str]) -> Preflight:
    """Can this update run, what will it do, and what should the admin know."""
    known = _table()
    selected = [k for k in keys() if k in set(component_keys) and k in known]

    if not selected:
        return Preflight(
            components=[],
            blocks=[
                Block(
                    component="",
                    code="nothing-selected",
                    message="No component was selected for update.",
                    resolutions=["cancel"],
                )
            ],
        )

    statuses = {s.key: s for s in await check()}
    blocks: list[Block] = []
    warnings: list[Warning] = []
    steps: list[str] = []

    for key in selected:
        component = known[key]
        current = statuses.get(key)

        # Only the repo facets that will actually be moved need a preflight.
        needs_repo = True
        if current is not None:
            repo_facet = current.facets.get("repo")
            needs_repo = repo_facet is None or not repo_facet.up_to_date
        if needs_repo:
            blocks.extend(await repo_blocks(component))

        if current is not None:
            steps.extend(_steps_for(key))

    if CONDOR in selected:
        condor_status = statuses.get(CONDOR)
        repo = condor_status.facets.get("repo") if condor_status else None
        if repo is not None and not repo.up_to_date:
            # What this update would actually write, rather than a diff against
            # an empty commit -- ``paths_changed`` answers True for an
            # unresolvable range by design, so asking it that way would make
            # both warnings below fire on every update.
            incoming = await updater.incoming_paths(_table()[CONDOR].repo_dir)
            for warning in _incoming_warnings(incoming or []):
                warnings.append(warning)
        disk_warning = _disk_warning(_table()[CONDOR].repo_dir)
        if disk_warning is not None:
            warnings.append(disk_warning)

        stale_warning = _stale_fork_warning()
        if stale_warning is not None:
            warnings.append(stale_warning)

        if await updater.has_update_stash(_table()[CONDOR].repo_dir):
            # Work a previous update parked and could not replay. Nothing said
            # so afterwards: the run ended, the step scrolled away, and the
            # stash sat there -- invisible to the next preflight, which only
            # looks at the working tree, and to `git status`, which does not
            # mention stashes at all.
            warnings.append(
                Warning(
                    component=CONDOR,
                    code="stashed-work",
                    message=(
                        "An earlier update parked local work in a stash and it "
                        "is still there — `git stash list` shows it as "
                        "`condor /update`. This update will try to replay it "
                        "again, and will put the checkout back untouched if it "
                        "still does not apply."
                    ),
                )
            )
        warnings.append(
            Warning(
                component=CONDOR,
                code="relaunch-to-apply",
                message=(
                    "Condor keeps running the code it booted with until you "
                    "relaunch it yourself — nothing is interrupted, and nothing "
                    "new takes effect either. Every surface will say so until "
                    "you do."
                ),
            )
        )

    return Preflight(components=selected, blocks=blocks, warnings=warnings, steps=steps)
