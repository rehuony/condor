"""Telegram profile photos for the admin people panel.

The panel names people by their Telegram identity, so it shows their Telegram
face too. The photo is fetched through the Bot API and proxied here rather than
linked: a Telegram file URL embeds the bot token, which must never reach a
browser.

Each answer is cached on disk for a day, including "no photo" (a person who has
none, or whose privacy settings hide it from bots), so a list of people costs
Telegram at most one round of calls per day. A failure to reach Telegram is not
cached: the next view tries again.

Admin-only like the rest of ``/admin``: a face is personal data.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from condor.paths import state_dir
from condor.web.auth import get_current_user, require_admin
from condor.web.models import WebUser

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

_TTL_SEC = 24 * 60 * 60
_TIMEOUT_SEC = 10
# The smallest square Telegram offers is 160px: sharp at the panel's 32px avatar
# on a 3x screen, and a few KB.
_MIN_SIDE = 96
_locks: dict[int, asyncio.Lock] = {}


def _cache_dir() -> Path:
    return state_dir("telegram_avatars")


def _token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN", "")


def _fresh(path: Path) -> bool:
    return path.exists() and time.time() - path.stat().st_mtime < _TTL_SEC


async def _fetch_photo(user_id: int) -> bytes | None:
    """The user's current profile photo, or None when Telegram shows bots none.

    Raises on transport errors, so the caller can tell "no photo" from
    "could not ask".
    """
    token = _token()
    if not token:
        return None
    api = f"https://api.telegram.org/bot{token}"
    async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
        response = await client.post(
            f"{api}/getUserProfilePhotos", json={"user_id": user_id, "limit": 1}
        )
        response.raise_for_status()
        photos = response.json()
        if not photos.get("ok"):
            raise RuntimeError("Telegram profile-photo request failed")
        sizes = (photos.get("result") or {}).get("photos") or []
        if not sizes or not sizes[0]:
            return None
        # Sizes come smallest first.
        size = next(
            (s for s in sizes[0] if s.get("width", 0) >= _MIN_SIDE), sizes[0][-1]
        )
        response = await client.post(
            f"{api}/getFile", json={"file_id": size["file_id"]}
        )
        response.raise_for_status()
        file = response.json()
        file_path = (file.get("result") or {}).get("file_path")
        if not file.get("ok") or not file_path:
            raise RuntimeError("Telegram photo-file request failed")
        image = await client.get(
            f"https://api.telegram.org/file/bot{token}/{file_path}"
        )
        image.raise_for_status()
        return image.content


def _photo_response(data: bytes) -> Response:
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/people/{user_id}/photo")
async def person_photo(user_id: int, user: WebUser = Depends(get_current_user)):
    """A person's Telegram profile photo; 404 when they have none to show."""
    require_admin(user)
    directory = _cache_dir()
    photo = directory / f"{user_id}.jpg"
    missing = directory / f"{user_id}.none"

    if _fresh(photo):
        return _photo_response(photo.read_bytes())
    if _fresh(missing):
        raise HTTPException(status_code=404, detail="No profile photo")

    # One fetch per person at a time: a list renders every row at once.
    async with _locks.setdefault(user_id, asyncio.Lock()):
        if _fresh(photo):
            return _photo_response(photo.read_bytes())
        if _fresh(missing):
            raise HTTPException(status_code=404, detail="No profile photo")
        try:
            data = await _fetch_photo(user_id)
        except Exception as e:  # noqa: BLE001 - Telegram unreachable is not a 500
            # HTTP errors include the request URL, which contains the bot token.
            log.info("person photo: could not fetch %s (%s)", user_id, type(e).__name__)
            raise HTTPException(status_code=502, detail="Telegram unavailable") from e

        directory.mkdir(parents=True, exist_ok=True)
        if data is None:
            photo.unlink(missing_ok=True)
            missing.touch()
            raise HTTPException(status_code=404, detail="No profile photo")
        tmp = photo.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(photo)
        missing.unlink(missing_ok=True)
        return _photo_response(data)
