"""Tests for whether this process is older than the code on disk.

``/relaunch`` feeds a banner on every page for every seat, admin or not, because the
mismatch it describes (new bundle, old API) is what a non-admin actually walks
into. So unlike the update routes it must NOT be admin-gated, and it must still
refuse an anonymous caller.
"""

import pytest
from starlette.testclient import TestClient

from condor.updates import run as run_mod
from condor.web.app import create_app
from condor.web.auth import get_current_user
from condor.web.models import WebUser


@pytest.fixture()
def app():
    return create_app()


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def _no_relaunch():
    """The engine's flag is process-wide module state; do not leak it."""
    run_mod._relaunch = None
    yield
    run_mod._relaunch = None


@pytest.fixture()
def seat(app):
    def _login(role: str = "user"):
        app.dependency_overrides[get_current_user] = lambda: WebUser(
            id=1, username="u", first_name="f", role=role
        )
        return TestClient(app)

    yield _login
    app.dependency_overrides.clear()


def test_relaunch_requires_authentication(client, _no_relaunch):
    assert client.get("/api/v1/meta/relaunch").status_code in (401, 403)


def test_nothing_owed_is_the_ordinary_answer(seat, _no_relaunch):
    """An install that has not updated in place says so in one field."""
    res = seat().get("/api/v1/meta/relaunch")
    assert res.status_code == 200
    assert res.json() == {"required": False}


def test_a_pending_relaunch_names_both_commits(seat, _no_relaunch):
    run_mod._relaunch = {
        "run_id": "u-1",
        "from_commit": "aaaaaaa1111111111111111111111111111111111",
        "target_commit": "bbbbbbb2222222222222222222222222222222222",
        "branch": "main",
        "at": 1756300000.0,
    }
    body = seat().get("/api/v1/meta/relaunch").json()

    assert body["required"] is True
    assert body["branch"] == "main"
    # Short shas: the banner renders them inline, and nobody reads forty chars.
    assert body["from_commit"] == "aaaaaaa"
    assert body["target_commit"] == "bbbbbbb"


def test_a_non_admin_seat_is_told_too(seat, _no_relaunch):
    """The stale-backend mismatch is not an admin-only experience."""
    run_mod._relaunch = {
        "run_id": "u-1",
        "from_commit": "aaaaaaa",
        "target_commit": "bbbbbbb",
        "branch": "main",
        "at": 1.0,
    }
    for role in ("user", "pending", "admin"):
        assert seat(role).get("/api/v1/meta/relaunch").json()["required"] is True
