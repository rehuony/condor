"""The admin panel's Telegram profile-photo proxy.

What has to hold: only admins get faces, Telegram is asked at most once a day
per person (a missing photo included), and an unreachable Telegram is reported
rather than remembered as "no photo".
"""

import asyncio

import pytest
from fastapi import HTTPException

from condor.web import auth as web_auth
from condor.web.models import WebUser
from condor.web.routes import admin_photos

ADMIN = WebUser(id=1, username="root", role="admin")
TRADER = WebUser(id=2, username="quant", role="user")


class _Roles:
    def is_admin(self, user_id):
        return user_id == ADMIN.id

    def get_user_role(self, user_id):
        return "user"


@pytest.fixture
def photos(tmp_path, monkeypatch):
    """Route state on a throwaway cache, with Telegram replaced by a counter."""
    monkeypatch.setattr(web_auth, "get_config_manager", lambda: _Roles())
    monkeypatch.setattr(admin_photos, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(admin_photos, "_locks", {})
    calls = []
    answers = {}

    async def fetch(user_id):
        calls.append(user_id)
        answer = answers.get(user_id)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(admin_photos, "_fetch_photo", fetch)
    return calls, answers, tmp_path


def get(user_id, actor=ADMIN):
    return asyncio.run(admin_photos.person_photo(user_id, user=actor))


def test_only_admins_see_faces(photos):
    with pytest.raises(HTTPException) as excinfo:
        get(7, actor=TRADER)
    assert excinfo.value.status_code == 403
    assert photos[0] == []


def test_a_photo_is_fetched_once_and_served_from_cache(photos):
    calls, answers, _ = photos
    answers[7] = b"jpeg-bytes"
    first = get(7)
    second = get(7)
    assert first.body == second.body == b"jpeg-bytes"
    assert first.media_type == "image/jpeg"
    assert calls == [7]


def test_no_photo_is_remembered_too(photos):
    calls, _, cache = photos
    for _ in range(2):
        with pytest.raises(HTTPException) as excinfo:
            get(8)
        assert excinfo.value.status_code == 404
    assert calls == [8]
    assert (cache / "8.none").exists()


def test_an_unreachable_telegram_is_retried_not_remembered(photos):
    calls, answers, cache = photos
    answers[9] = RuntimeError("network down")
    with pytest.raises(HTTPException) as excinfo:
        get(9)
    assert excinfo.value.status_code == 502
    assert not (cache / "9.none").exists()

    answers[9] = b"later"
    assert get(9).body == b"later"
    assert calls == [9, 9]


def test_a_photo_that_went_away_replaces_the_cached_one(photos, monkeypatch):
    calls, answers, cache = photos
    answers[10] = b"old"
    get(10)
    monkeypatch.setattr(admin_photos, "_TTL_SEC", 0)
    answers[10] = None
    with pytest.raises(HTTPException):
        get(10)
    assert not (cache / "10.jpg").exists()
    assert calls == [10, 10]


def _telegram(monkeypatch, photos_result):
    """Point the fetcher at a fake Bot API and record what it asked for."""
    import httpx

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/getUserProfilePhotos"):
            return httpx.Response(200, json={"ok": True, "result": photos_result})
        if request.url.path.endswith("/getFile"):
            file_id = request.read().decode()
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "file_path": f"photos/{'mid' if 'mid' in file_id else 'x'}.jpg"
                    },
                },
            )
        return httpx.Response(200, content=b"image:" + request.url.path.encode())

    real = httpx.AsyncClient
    monkeypatch.setattr(
        admin_photos.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setenv("TELEGRAM_TOKEN", "secret-token")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    return seen


def test_fetch_picks_the_smallest_sharp_size(monkeypatch):
    seen = _telegram(
        monkeypatch,
        {
            "photos": [
                [
                    {"file_id": "tiny", "width": 80},
                    {"file_id": "mid", "width": 160},
                    {"file_id": "big", "width": 640},
                ]
            ]
        },
    )
    data = asyncio.run(admin_photos._fetch_photo(7))
    assert data == b"image:/file/botsecret-token/photos/mid.jpg"
    assert seen[-1] == "/file/botsecret-token/photos/mid.jpg"


def test_fetch_reports_a_user_without_photos_as_none(monkeypatch):
    _telegram(monkeypatch, {"total_count": 0, "photos": []})
    assert asyncio.run(admin_photos._fetch_photo(7)) is None


@pytest.mark.parametrize("method", ["getUserProfilePhotos", "getFile"])
@pytest.mark.parametrize("status", [200, 429, 500])
def test_bot_api_errors_do_not_become_cached_missing_photos(
    tmp_path, monkeypatch, method, status
):
    import httpx

    calls = []

    def handler(request):
        requested = request.url.path.rsplit("/", 1)[-1]
        calls.append(requested)
        if requested == method:
            return httpx.Response(status, json={"ok": False, "description": "Failed"})
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"photos": [[{"file_id": "photo", "width": 160}]]},
            },
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        admin_photos.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setattr(admin_photos, "_token", lambda: "test-token")
    monkeypatch.setattr(admin_photos, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(admin_photos, "_locks", {})
    monkeypatch.setattr(admin_photos, "require_admin", lambda user: None)

    for _ in range(2):
        with pytest.raises(HTTPException) as excinfo:
            get(7)
        assert excinfo.value.status_code == 502
        assert not (tmp_path / "7.none").exists()
    assert calls.count(method) == 2


def test_telegram_failure_logs_do_not_include_the_bot_token(photos, caplog):
    import logging

    import httpx

    request = httpx.Request(
        "GET", "https://api.telegram.org/file/botprivate-test-token/photos/a.jpg"
    )
    response = httpx.Response(502, request=request)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        photos[1][7] = error

    with caplog.at_level(logging.INFO, logger=admin_photos.__name__):
        with pytest.raises(HTTPException) as excinfo:
            get(7)
    assert excinfo.value.status_code == 502
    assert "private-test-token" not in caplog.text
