"""Deletion preserves upstream conflicts, respects server access and evicts history caches."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from condor.asyncutil import SingleFlight
from condor.server_data_service import ServerDataService, ServerDataType
from condor.web.auth import require_server_access
from condor.web.models import WebUser
from condor.web.routes import executors


class Response:
    def __init__(self, status, body):
        self.status = status
        self.ok = 200 <= status < 300
        self.body = body
        self.request_info = None
        self.history = ()
        self.headers = {}

    async def json(self):
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.fixture
def env(monkeypatch):
    session = SimpleNamespace(
        delete=Mock(
            return_value=Response(200, {"deleted": True, "executor_id": "ex-1"})
        )
    )
    client = SimpleNamespace(
        bot_orchestration=SimpleNamespace(
            session=session, base_url="http://private:8000"
        )
    )
    manager = SimpleNamespace(get_client=AsyncMock(return_value=client))
    sds = SimpleNamespace(invalidate=Mock())
    deed = Mock()
    monkeypatch.setattr(executors, "get_config_manager", lambda: manager)
    monkeypatch.setattr(executors, "get_server_data_service", lambda: sds)
    monkeypatch.setattr(executors, "record_ui_deed", deed)
    monkeypatch.setattr(executors, "_summary_generation", {})
    monkeypatch.setattr(
        executors,
        "_summary_cache",
        {
            ("local", "1D"): (0, "stale"),
            ("local", "1M"): (0, "stale"),
            ("other", "1D"): (0, "keep"),
        },
    )
    app = FastAPI()
    app.include_router(executors.router)
    app.dependency_overrides[require_server_access] = lambda: WebUser(
        id=1, role="admin"
    )
    return SimpleNamespace(
        http=TestClient(app),
        app=app,
        session=session,
        sds=sds,
        deed=deed,
        manager=manager,
    )


def test_delete_through_http_uses_delete_and_invalidates_only_its_server(env):
    response = env.http.delete("/servers/local/executors/ex-1")
    assert response.status_code == 200
    assert response.json() == {"deleted": True, "executor_id": "ex-1"}
    env.session.delete.assert_called_once_with("http://private:8000/executors/ex-1")
    env.sds.invalidate.assert_called_once_with("local", ServerDataType.EXECUTORS)
    assert executors._summary_cache == {("other", "1D"): (0, "keep")}
    assert env.deed.call_args.kwargs["verb"] == "delete_executor"


@pytest.mark.parametrize("upstream,status", [(404, 404), (409, 409), (500, 502)])
def test_rejected_delete_keeps_caches_and_reports_failure(env, upstream, status):
    env.session.delete.return_value = Response(
        upstream, {"detail": "Cannot delete record"}
    )
    response = env.http.delete("/servers/local/executors/ex-1")
    assert response.status_code == status
    assert "Cannot delete record" in response.json()["detail"]
    assert "private" not in response.text
    env.sds.invalidate.assert_not_called()
    env.deed.assert_not_called()
    assert ("local", "1D") in executors._summary_cache


def test_delete_is_not_reachable_without_authentication(env):
    env.app.dependency_overrides.clear()
    response = env.http.delete("/servers/local/executors/ex-1")
    assert response.status_code in (401, 403)
    env.manager.get_client.assert_not_called()


def test_executor_id_is_encoded_before_forwarding(env):
    asyncio.run(
        executors.delete_executor_endpoint(
            "local", "id?with=query", WebUser(id=1, role="admin")
        )
    )
    env.session.delete.assert_called_once_with(
        "http://private:8000/executors/id%3Fwith%3Dquery"
    )


def test_a_walk_started_before_deletion_cannot_restore_stale_totals(env, monkeypatch):
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        async def fetch(_client):
            started.set()
            await release.wait()
            return []

        monkeypatch.setattr(executors, "fetch_all_executors", fetch)
        monkeypatch.setattr(
            executors, "_usd_summary", AsyncMock(return_value="old summary")
        )
        walk = asyncio.create_task(executors._walk_and_summarize("local", object()))
        await started.wait()
        await executors.delete_executor_endpoint(
            "local", "ex-1", WebUser(id=1, role="admin")
        )
        release.set()
        await walk
        assert executors._summary_cache == {("other", "1D"): (0, "keep")}

    asyncio.run(scenario())


@pytest.mark.parametrize("fail_old_read", [False, True])
@pytest.mark.parametrize("fresh_cached", [False, True])
def test_summary_started_before_deletion_returns_current_totals(
    env, monkeypatch, fail_old_read, fresh_cached
):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0
        rows = [
            {
                "id": executor_id,
                "config": {
                    "trading_pair": "BTC-USDT",
                    "timestamp": time.time(),
                },
                "status": "TERMINATED",
                "net_pnl_quote": pnl,
            }
            for executor_id, pnl in (("ex-1", 7), ("keep", -2))
        ]

        async def fetch(_client):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
                if fail_old_read:
                    raise RuntimeError("old history read failed")
                return rows
            return rows[1:]

        monkeypatch.setattr(executors, "fetch_all_executors", fetch)
        monkeypatch.setattr(executors, "_summary_cache", {})
        monkeypatch.setattr(executors, "_summary_walks", SingleFlight())
        user = WebUser(id=1, role="admin")
        old_read = asyncio.create_task(executors.executors_summary("local", "1D", user))
        await asyncio.wait_for(started.wait(), timeout=1)
        try:
            await executors.delete_executor_endpoint("local", "ex-1", user)
            if fresh_cached:
                fresh = await executors.executors_summary("local", "1D", user)
                assert (fresh.count, fresh.pnl) == (1, -2)
        finally:
            release.set()

        result = await asyncio.wait_for(old_read, timeout=1)
        assert (result.count, result.pnl) == (1, -2)
        assert calls == 2, "retired readers must share the current history result"

    asyncio.run(scenario())


@pytest.mark.parametrize("fail_old_read", [False, True])
def test_deletion_retires_inflight_cache_reads(env, monkeypatch, fail_old_read):
    async def scenario():
        sds = ServerDataService()
        sds._get_client = AsyncMock(return_value=object())
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def fetch(_client):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
                if fail_old_read:
                    raise RuntimeError("read failed")
                return [{"id": "ex-1"}]
            return []

        sds.register_fetch(ServerDataType.EXECUTORS, fetch)
        sds.put("other", ServerDataType.EXECUTORS, [{"id": "keep"}])
        published = []
        sds.add_listener(lambda key, value: published.append(value))
        monkeypatch.setattr(executors, "get_server_data_service", lambda: sds)

        old_read = asyncio.create_task(
            sds.get_or_fetch("local", ServerDataType.EXECUTORS)
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        try:
            await executors.delete_executor_endpoint(
                "local", "ex-1", WebUser(id=1, role="admin")
            )
            assert (
                await asyncio.wait_for(
                    sds.get_or_fetch("local", ServerDataType.EXECUTORS), timeout=1
                )
                == []
            )
        finally:
            release.set()

        assert await old_read == []
        assert sds.get("local", ServerDataType.EXECUTORS) == []
        assert sds.get_entry("local", ServerDataType.EXECUTORS).consecutive_errors == 0
        assert sds.get("other", ServerDataType.EXECUTORS) == [{"id": "keep"}]
        assert published == [[]]
        assert calls == 2

    asyncio.run(scenario())


def test_old_running_api_explains_required_update(env):
    env.session.delete.return_value = Response(405, {"detail": "Method Not Allowed"})
    response = env.http.delete("/servers/local/executors/ex-1")
    assert response.status_code == 502
    assert "Update the running Hummingbot API" in response.json()["detail"]
    env.sds.invalidate.assert_not_called()
    env.deed.assert_not_called()
