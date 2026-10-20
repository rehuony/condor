"""Concurrent status merges preserve both writers' fields.

Atomic rename protects readers; the per-file lock also protects the complete
read-modify-write operation when a worker and the event loop write concurrently.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import threading
import time

import pytest

from condor.runtime import registry_file

# ── The general property ─────────────────────────────────────────────────


@pytest.fixture
def widened_write(monkeypatch):
    """Stretch the read→rename window so the race is reachable in a test.

    Production loses this race on a GIL release inside ``read_text`` or
    ``fsync``; half a millisecond of sleep makes the same window wide enough to
    hit reliably. It sits *inside* the lock, so it slows the guarded version
    down without changing what it guarantees.
    """
    real = registry_file.atomic_write_json

    def slow_write(path, data, **kwargs):
        time.sleep(0.0005)
        return real(path, data, **kwargs)

    monkeypatch.setattr(registry_file, "atomic_write_json", slow_write)


def test_disjoint_merges_from_two_threads_both_survive(tmp_path, widened_write):
    """Two threads, two field sets, one file: the result carries both."""
    for i in range(40):
        record = tmp_path / f"record-{i}"
        record.mkdir()

        threads = [
            threading.Thread(
                target=registry_file.write_status, args=(record,), kwargs=fields
            )
            for fields in ({"alpha": i}, {"beta": i})
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        status = registry_file.read_status(record)
        assert status is not None
        assert status["alpha"] == i, f"iteration {i} lost the first writer"
        assert status["beta"] == i, f"iteration {i} lost the second writer"


def test_write_status_stays_synchronous():
    """The lock is held across the read, the merge and the rename — so none of
    those may ever become an await. A lock held across one would block every
    other writer for the length of an I/O round trip."""
    assert not inspect.iscoroutinefunction(registry_file.write_status)
    tree = ast.parse(textwrap.dedent(inspect.getsource(registry_file.write_status)))
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Await)]
