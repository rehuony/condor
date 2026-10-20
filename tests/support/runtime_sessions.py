"""Session-registry fixtures for confirmation and teardown behavior."""


class _DeadClient:
    """Minimal stand-in for an agent client, for the teardown paths."""

    def __init__(self, alive: bool = True):
        self.alive = alive
        self.stop_calls = 0

    async def stop(self):
        self.stop_calls += 1
        self.alive = False


def install_session(monkeypatch, key, *, alive=True, user_id=1, **fields):
    """Put one live-looking session behind ``key`` in the session registry."""
    from condor.runtime import sessions as sessions_module

    session = sessions_module.AgentSession(
        key=key,
        agent_key="claude-code",
        client=_DeadClient(alive),
        user_id=user_id,
        **fields,
    )
    monkeypatch.setitem(sessions_module._sessions, str(key), session)
    return session
