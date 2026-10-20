"""UserStateStore: the dicts behind context.user_data and their file."""

from __future__ import annotations

import asyncio
import pickle

import pytest

from condor.telegram import state as state_mod
from condor.telegram.state import UserStateStore


@pytest.fixture
def store(tmp_path):
    return UserStateStore(tmp_path / "state.pickle")


def test_same_id_returns_the_same_object(store):
    first = store.get(7)
    first["k"] = 1
    assert store.get(7) is first
    assert store.users[7] is first
    assert store.chat(9) is store.chat(9)


def test_flush_strips_ephemeral_but_keeps_it_live(store):
    live = store.get(1)
    live["user_preferences"] = {"quote": "USDC"}
    live["_cache"] = {"big": [0] * 1000}
    store.chat(5)["_secret_notice_seen"] = {"x": True}

    store.flush()

    on_disk = pickle.loads(store.path.read_bytes())
    assert on_disk["version"] == 1
    assert on_disk["users"][1] == {"user_preferences": {"quote": "USDC"}}
    assert on_disk["chats"][5] == {"_secret_notice_seen": {"x": True}}
    assert "_cache" in live


def test_unpicklable_values_are_dropped_not_fatal(store, caplog):
    async def main():
        task = asyncio.create_task(asyncio.sleep(10))
        live = store.get(2)
        live["loading_task"] = task
        live["lock"] = asyncio.Lock()
        live["client"] = lambda: None  # lambdas cannot be pickled
        live["pair"] = "SOL-USDC"
        store.flush()
        task.cancel()

    asyncio.run(main())

    on_disk = pickle.loads(store.path.read_bytes())
    assert on_disk["users"][2] == {"pair": "SOL-USDC"}
    assert "Dropping unpicklable" in caplog.text
    # The live dict still has everything.
    assert set(store.get(2)) == {"loading_task", "lock", "client", "pair"}


def test_reload_round_trips_and_keeps_bak(store):
    store.get(3)["a"] = 1
    store.flush()
    store.get(3)["a"] = 2
    store.flush()

    assert pickle.loads(store._bak.read_bytes())["users"][3] == {"a": 1}

    fresh = UserStateStore(store.path)
    fresh.load()
    assert fresh.get(3) == {"a": 2}


def test_corrupt_main_file_falls_back_to_bak(store):
    store.get(4)["a"] = 1
    store.flush()
    store.get(4)["a"] = 2
    store.flush()
    store.path.write_bytes(b"garbage")

    fresh = UserStateStore(store.path)
    fresh.load()
    assert fresh.get(4) == {"a": 1}


def test_failed_write_keeps_the_previous_file(store, monkeypatch):
    store.get(5)["a"] = 1
    store.flush()
    before = store.path.read_bytes()

    def boom(path, data, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(state_mod, "atomic_write_bytes", boom)
    store.get(5)["a"] = 2
    with pytest.raises(OSError):
        store.flush()

    # The rotate moved the previous snapshot to .bak; nothing was lost and
    # the next loader recovers it.
    fresh = UserStateStore(store.path)
    fresh.load()
    assert fresh.get(5) == {"a": 1}
    assert store._bak.read_bytes() == before


def test_identical_snapshot_is_not_rewritten(store):
    store.get(6)["a"] = 1
    store.flush()
    first = store.path.stat().st_mtime_ns
    store.flush()
    assert store.path.stat().st_mtime_ns == first


def test_flush_loop_runs_and_close_writes_last(store):
    async def main():
        await store.start(interval=0.05)
        store.get(8)["a"] = 1
        store.mark_dirty()
        await asyncio.sleep(0.2)
        first = pickle.loads(store.path.read_bytes())["users"][8]
        store.get(8)["a"] = 2
        await store.close()
        return first

    first = asyncio.run(main())
    assert first == {"a": 1}
    assert pickle.loads(store.path.read_bytes())["users"][8] == {"a": 2}


def test_the_app_serves_the_store_dicts_read_only(store):
    from condor.telegram.app import TelegramApp

    application = TelegramApp("0:local", store)

    store.get(21)["x"] = 1
    assert application.user_data[21] is store.get(21)
    assert application.chat_data[22] is store.chat(22)
    with pytest.raises(TypeError):
        application.user_data[23] = {}  # still a read-only view
