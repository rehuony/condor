"""Tell the dashboard whether this process needs a relaunch.

An update lands new code and rebuilds the dashboard bundle, but does not exec
itself (see :mod:`condor.updates.run`), so between the update and the relaunch
the browser is running new frontend against old backend. Every seat is told,
not just the admin's: the mismatch is what a non-admin actually experiences,
and "relaunch to apply" explains a page that otherwise just looks broken.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from condor import updates
from condor.web.auth import get_current_user
from condor.web.models import WebUser

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/relaunch")
async def get_relaunch(user: WebUser = Depends(get_current_user)) -> dict:
    """Whether this process is older than the code on disk, and by how much.

    ``required`` is false for every install that has not updated in place —
    which is nearly all of them, nearly all of the time — so the banner this
    feeds costs one cheap poll and renders nothing.
    """
    pending = updates.relaunch_pending()
    if pending is None:
        return {"required": False}
    return {
        "required": True,
        "branch": pending.get("branch") or "",
        "from_commit": (pending.get("from_commit") or "")[:7],
        "target_commit": (pending.get("target_commit") or "")[:7],
        "at": pending.get("at"),
    }
