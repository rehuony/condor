"""Deletion preserves upstream conflicts, respects server access and evicts history caches."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from condor.server_data_service import ServerDataType
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
