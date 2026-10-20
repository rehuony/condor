"""A timed-out worker leaves recoverable progress and a truthful final state."""

import asyncio
import json
from dataclasses import replace

import pytest

from condor import paths
from condor.acp.client import PromptDone, TextChunk, ThoughtChunk
from condor.agents import agent_run
from condor.agents import delegate as delegations
from condor.agents.agent import Agent
from condor.agents.delegation_history import read_history


@pytest.fixture(autouse=True)
def registry():
    delegations._delegations.clear()
    yield
    delegations._delegations.clear()


async def _start(**kwargs):
    return await delegations.start_delegation(
        agent_slug="scout",
        user_id=1,
        chat_id=0,
        server_name=None,
        task="build",
        **kwargs,
    )


def test_timeout_keeps_public_progress_on_disk_and_in_completion(monkeypatch):
    async def work(**kwargs):
        kwargs["event_sink"](ThoughtChunk(text="private reasoning"))
        kwargs["event_sink"](TextChunk(text="Saved checkpoint.json; tests pending"))
        await asyncio.Event().wait()

    monkeypatch.setattr(agent_run, "run_agent_to_completion", work)

    async def scenario():
        dt = await _start(timeout_s=0.01)
        await dt._task
        return dt

    dt = asyncio.run(scenario())
    assert dt.status == "error"
    assert dt.error == "Timed out after 0.01s"
    assert dt.result == "Saved checkpoint.json; tests pending"
    assert "task incomplete" in delegations._completion_text(dt)
    assert "private reasoning" not in delegations._completion_text(dt)
    assert read_history(1, dt.task_id)["result"] == dt.result
    events = paths.delegation_dir(1, dt.task_id) / "events.json"
    assert json.loads(events.read_text())["events"]


def test_internal_timeout_is_not_misreported_as_task_budget(monkeypatch):
    async def work(**kwargs):
        raise TimeoutError("market data request expired")

    monkeypatch.setattr(agent_run, "run_agent_to_completion", work)

    async def scenario():
        dt = await _start(timeout_s=60)
        await dt._task
        return dt

    dt = asyncio.run(scenario())
    assert dt.error == "market data request expired"
    assert dt.status == "error"


@pytest.mark.parametrize("stop_manually", [False, True])
def test_swallowed_cancellation_never_becomes_success(monkeypatch, stop_manually):
    async def work(**kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return "unfinished work"

    monkeypatch.setattr(agent_run, "run_agent_to_completion", work)

    async def scenario():
        dt = await _start(timeout_s=0.02)
        if stop_manually:
            await asyncio.sleep(0)
            assert await delegations.stop_delegation(dt.task_id)
        try:
            await dt._task
        except asyncio.CancelledError:
            pass
        return dt

    dt = asyncio.run(scenario())
    assert dt.status == ("stopped" if stop_manually else "error")
    assert dt.result == "unfinished work"
    assert read_history(1, dt.task_id)["status"] == dt.status


def test_stop_before_runner_starts_is_persisted(monkeypatch):
    async def work(**kwargs):
        pytest.fail("A stopped worker must never start")

    monkeypatch.setattr(agent_run, "run_agent_to_completion", work)

    async def scenario():
        dt = await _start()
        assert await delegations.stop_delegation(dt.task_id)
        assert not await delegations.stop_delegation(dt.task_id)
        with pytest.raises(asyncio.CancelledError):
            await dt._task
        return dt

    dt = asyncio.run(scenario())
    assert read_history(1, dt.task_id)["status"] == "stopped"


def _engine(monkeypatch, client):
    from condor.runtime import llm_client

    monkeypatch.setattr(
        agent_run.AgentStore, "get", lambda self, slug: Agent(slug=slug, name=slug)
    )
    monkeypatch.setattr(
        agent_run, "resolve_custom_endpoint", lambda *a, **k: (None, None)
    )
    monkeypatch.setattr(
        agent_run.toolsets, "build_mcp_servers_for_session", lambda *a, **k: []
    )
    monkeypatch.setattr(
        agent_run.runtime_context, "build_agent_context", lambda *a: "prompt"
    )
    monkeypatch.setattr(llm_client, "build_llm_client", lambda *a, **k: client)


class _Client:
    def __init__(self):
        self.stopped = False
        self.budget = None

    async def start(self):
        pass

    async def stop(self):
        self.stopped = True

    async def prompt_stream(self, text, *, timeout_s=None):
        self.budget = timeout_s
        yield TextChunk(text="done")
        yield PromptDone(stop_reason="end_turn")


def test_background_budget_reaches_the_real_engine_client(monkeypatch):
    client = _Client()
    _engine(monkeypatch, client)

    async def scenario():
        dt = await _start(timeout_s=7200)
        await dt._task
        return dt

    dt = asyncio.run(scenario())
    assert dt.status == "done"
    assert client.budget == 7200 + agent_run.TIMEOUTS.agent_cleanup
    assert client.stopped


def test_cleanup_error_does_not_mask_session_failure(monkeypatch):
    class Client(_Client):
        async def prompt_stream(self, text, **kwargs):
            yield PromptDone(stop_reason="disconnected")

        async def stop(self):
            raise RuntimeError("cleanup error")

    _engine(monkeypatch, Client())
    with pytest.raises(RuntimeError, match="agent session ended: disconnected"):
        asyncio.run(agent_run.run_agent_to_completion("scout", 1, 0, None, "build"))


@pytest.mark.parametrize("stop_manually", [False, True])
def test_cancellation_during_teardown_waits_for_cleanup(monkeypatch, stop_manually):
    class Client(_Client):
        async def stop(self):
            await asyncio.sleep(0.05)
            self.stopped = True

    client = Client()
    _engine(monkeypatch, client)

    async def scenario():
        dt = await _start(timeout_s=0.02)
        if stop_manually:
            await asyncio.sleep(0.01)
            await delegations.stop_delegation(dt.task_id)
        try:
            await asyncio.wait_for(dt._task, timeout=1)
        except asyncio.CancelledError:
            pass
        assert client.stopped
        return dt

    dt = asyncio.run(scenario())
    assert dt.status == ("stopped" if stop_manually else "error")


@pytest.mark.parametrize("failure", ["error", "cancel"])
def test_startup_failure_still_cleans_up(monkeypatch, failure):
    class Client(_Client):
        async def start(self):
            if failure == "cancel":
                raise asyncio.CancelledError
            raise RuntimeError("startup failed")

    client = Client()
    _engine(monkeypatch, client)
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else RuntimeError):
        asyncio.run(agent_run.run_agent_to_completion("scout", 1, 0, None, "build"))
    assert client.stopped


@pytest.mark.parametrize("cleanup", ["hang", "error"])
def test_cleanup_does_not_mask_timeout(monkeypatch, cleanup):
    class Client(_Client):
        async def prompt_stream(self, text, **kwargs):
            yield TextChunk(text="checkpoint saved")
            await asyncio.Event().wait()

        async def stop(self):
            self.stopped = True
            if cleanup == "hang":
                await asyncio.Event().wait()
            raise RuntimeError("cleanup failed")

    client = Client()
    _engine(monkeypatch, client)
    monkeypatch.setattr(
        agent_run, "TIMEOUTS", replace(agent_run.TIMEOUTS, agent_cleanup=0.02)
    )

    async def scenario():
        dt = await _start(timeout_s=0.01)
        await asyncio.wait_for(dt._task, timeout=1)
        return dt

    dt = asyncio.run(scenario())
    assert client.stopped
    assert dt.error == "Timed out after 0.01s"
    assert dt.result == "checkpoint saved"
