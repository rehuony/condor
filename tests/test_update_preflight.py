"""What blocks an update.

``/update`` used to refuse whenever ``git status --porcelain`` printed anything,
which includes untracked files. A checkout that is also a runtime working
directory re-dirties itself by running, so that guard blocked forever, by
construction. The question is not "is the tree clean", it is "would
fast-forwarding clobber something", and that is the intersection of the dirty
set with the incoming diff.
"""

import asyncio
from unittest.mock import AsyncMock, patch

from condor.updates import components
from utils import updater
from utils.updater import DirtyState


def _component(key=components.CONDOR, repo_dir="/tmp/repo"):
    return components.Component(key, "Condor", repo_dir)


def _blocks(
    *,
    dirty: DirtyState,
    incoming: list[str] | None,
    ahead: int = 0,
    is_repo=True,
    fetched=True,
):
    """Run the blocker policy against a synthetic working tree.

    ``incoming=None`` stands for a diff that did not resolve, ``fetched=False``
    for a remote that could not be reached -- neither is an empty diff.
    """
    with patch.multiple(
        "utils.updater",
        is_git_repo=AsyncMock(return_value=is_repo),
        is_detached=AsyncMock(return_value=False),
        fetch=AsyncMock(return_value=(fetched, "" if fetched else "no such remote")),
        get_current_branch=AsyncMock(return_value="main"),
        ahead_count=AsyncMock(return_value=ahead),
        dirty_state=AsyncMock(return_value=dirty),
        incoming_paths=AsyncMock(return_value=incoming),
    ):
        return asyncio.run(components.repo_blocks(_component()))


def test_the_blockers_are_computed_against_a_freshly_fetched_origin():
    """A stale origin/ makes the incoming set a lie, and the lie is silent."""
    fetch = AsyncMock(return_value=(True, ""))
    with patch.multiple(
        "utils.updater",
        is_git_repo=AsyncMock(return_value=True),
        is_detached=AsyncMock(return_value=False),
        fetch=fetch,
        get_current_branch=AsyncMock(return_value="main"),
        ahead_count=AsyncMock(return_value=0),
        dirty_state=AsyncMock(return_value=DirtyState()),
        incoming_paths=AsyncMock(return_value=[]),
    ):
        asyncio.run(components.repo_blocks(_component()))
    fetch.assert_awaited_once()


# ---------------------------------------------------------------------------
# The blocker set
# ---------------------------------------------------------------------------


def test_a_dirty_file_the_update_would_overwrite_blocks():
    blocks = _blocks(
        dirty=DirtyState(modified=("environment.yml",)),
        incoming=["environment.yml", "routers/bots.py"],
    )
    assert [b.code for b in blocks] == ["dirty-conflict"]
    assert blocks[0].paths == ["environment.yml"]
    assert set(blocks[0].resolutions) == {"discard", "stash", "cancel"}


def test_a_dirty_file_the_update_never_touches_does_not_block():
    """The whole defect: local work outside the incoming diff is not a conflict."""
    assert (
        _blocks(
            dirty=DirtyState(modified=("environment.yml",)),
            incoming=["routers/bots.py"],
        )
        == []
    )


def test_untracked_junk_never_blocks():
    """A .DS_Store is not in anybody's incoming diff, so it is not in the way."""
    assert (
        _blocks(
            dirty=DirtyState(untracked=("bots/archived/.DS_Store", "bots/data/x.db")),
            incoming=["routers/bots.py"],
        )
        == []
    )


def test_an_untracked_file_the_update_would_write_blocks():
    blocks = _blocks(
        dirty=DirtyState(untracked=("claude.md",)),
        incoming=["claude.md"],
    )
    assert [b.code for b in blocks] == ["untracked-conflict"]
    assert blocks[0].paths == ["claude.md"]


def test_both_kinds_of_conflict_are_reported_apart():
    blocks = _blocks(
        dirty=DirtyState(modified=("environment.yml",), untracked=("claude.md",)),
        incoming=["environment.yml", "claude.md"],
    )
    assert [b.code for b in blocks] == ["dirty-conflict", "untracked-conflict"]


def test_staged_changes_count_as_dirty():
    blocks = _blocks(
        dirty=DirtyState(staged=("config.py",)),
        incoming=["config.py"],
    )
    assert [b.code for b in blocks] == ["dirty-conflict"]


def test_local_commits_ahead_report_diverged_and_offer_no_button():
    """A merge commit produced from a Telegram button is nobody's intent."""
    blocks = _blocks(dirty=DirtyState(), incoming=["a.py"], ahead=3)
    assert [b.code for b in blocks] == ["diverged"]
    assert blocks[0].resolutions == ["cancel"]
    assert "3 commits" in blocks[0].message


def test_a_directory_that_is_not_a_checkout_says_so():
    blocks = _blocks(dirty=DirtyState(), incoming=[], is_repo=False)
    assert [b.code for b in blocks] == ["not-a-repo"]


def test_nothing_incoming_means_nothing_can_conflict():
    blocks = _blocks(
        dirty=DirtyState(modified=("a.py",), untracked=("b.py",)),
        incoming=[],
    )
    assert blocks == []


# ---------------------------------------------------------------------------
# An unanswered question is not an answer of "no"
# ---------------------------------------------------------------------------


def test_an_empty_diff_and_an_unresolvable_one_do_not_land_in_the_same_place():
    """The defect, pinned as a contrast: [] cleared the update, and so did failure.

    ``incoming_paths`` used to return ``[]`` for a diff that never ran, which
    ``repo_blocks`` read as "nothing is coming in" and therefore "nothing is in
    the way" — a failure that green-lit the very fast-forward it was asked to
    gate. Same dirty tree, two different states, two different verdicts.
    """
    dirty = DirtyState(modified=("environment.yml",))

    assert _blocks(dirty=dirty, incoming=[]) == []

    blocks = _blocks(dirty=dirty, incoming=None)
    assert [b.code for b in blocks] == ["incoming-unknown"]
    assert blocks[0].resolutions == ["cancel"]
    assert blocks[0].paths == []
    assert "origin/main" in blocks[0].message


def test_a_fetch_that_failed_blocks_rather_than_judging_from_a_stale_ref():
    """No fetch, no basis: the incoming set below it would be about yesterday."""
    blocks = _blocks(dirty=DirtyState(), incoming=[], fetched=False)
    assert [b.code for b in blocks] == ["incoming-unknown"]
    assert blocks[0].resolutions == ["cancel"]


def test_an_unresolvable_diff_blocks_even_on_a_pristine_tree():
    """Nothing dirty to clobber today, but nobody checked what lands tomorrow."""
    blocks = _blocks(dirty=DirtyState(), incoming=None)
    assert [b.code for b in blocks] == ["incoming-unknown"]


def test_incoming_paths_returns_none_when_the_diff_fails():
    """The primitive's half of the contract: rc != 0 is None, never []."""
    with patch.object(
        updater, "_run_git", AsyncMock(return_value=(128, ""))
    ) as run_git:
        assert asyncio.run(updater.incoming_paths("/tmp/repo", "main")) is None
    assert run_git.await_args.args == ("diff", "--name-only", "HEAD..origin/main")


def test_incoming_paths_returns_an_empty_list_when_the_diff_is_empty():
    with patch.object(updater, "_run_git", AsyncMock(return_value=(0, "\n"))):
        assert asyncio.run(updater.incoming_paths("/tmp/repo", "main")) == []


def test_runtime_output_never_blocks_and_a_local_pin_blocks_only_when_touched():
    """Runtime output everywhere, one deliberate local edit."""
    dirty = DirtyState(
        modified=("Makefile",),
        untracked=(
            "data/condor_state.pickle",
            "reports/2026-10-20/report.html",
            "agents/local/notes.md",
            ".condor/research/topic/report.md",
        ),
    )
    incoming = ["condor/updates/run.py", "handlers/admin/update.py"]
    assert _blocks(dirty=dirty, incoming=incoming) == []

    # And when upstream *does* touch the pinned file, that one path blocks.
    blocks = _blocks(dirty=dirty, incoming=incoming + ["Makefile"])
    assert [b.paths for b in blocks] == [["Makefile"]]
