"""The dashboard's uvicorn server: its bind address and who can reach it."""

import logging
import os

from utils.config import LOCAL_MODE, USE_TAILSCALE, WEB_HOST, WEB_PORT

logger = logging.getLogger(__name__)


def web_server_config(web_app):
    """uvicorn's config for the dashboard, isolated so the bind address is testable.

    ``WEB_HOST`` is ``0.0.0.0`` in telegram mode (unchanged) and loopback in
    local mode, where the dashboard has no login at all, or when Tailscale is
    enabled, where `tailscale serve` is what actually exposes it — see
    :func:`utils.config.resolve_web_host` for why that is not negotiable by
    accident.
    """
    import uvicorn

    return uvicorn.Config(
        web_app,
        host=WEB_HOST,
        port=WEB_PORT,
        log_level="info",
        access_log=False,
    )


async def ensure_dashboard_reachable() -> None:
    """Report where the dashboard is reachable, proxying it via Tailscale if needed.

    With Tailscale on, ``WEB_HOST`` (:func:`utils.config.resolve_web_host`) is
    normally this node's own tailnet address, which needs no proxy: the port is
    on the tailnet and on no public interface, full stop.

    `tailscale serve` is only the fallback for when that address could not be
    resolved -- daemon not up yet, node not registered -- in which case the
    bind failed closed to loopback and a proxy is the only way anything reaches
    it. Serve needs the daemon socket, which refuses unprivileged callers, so
    this often cannot succeed either; say so plainly rather than leaving the
    dashboard silently unreachable.
    """
    if not USE_TAILSCALE:
        return
    if LOCAL_MODE:
        # Local mode keeps loopback even with Tailscale on: the dashboard has
        # no login, and a tailnet is a smaller audience than the internet, not
        # an authenticated one. Intended, so no proxy and no warning.
        logger.info(
            "Dashboard on http://%s:%s — local mode stays on loopback even "
            "with Tailscale enabled (no login on this dashboard).",
            WEB_HOST,
            WEB_PORT,
        )
        return

    from utils.tailscale import is_tailnet_ip

    if is_tailnet_ip(WEB_HOST):
        logger.info(
            "Dashboard on http://%s:%s — reachable on the tailnet only.",
            WEB_HOST,
            WEB_PORT,
        )
    elif os.environ.get("WEB_HOST", "").strip():
        # An explicit WEB_HOST is a deliberate opt-out, not a failed lookup.
        # Warning about a tailnet address we were never asked to find would
        # report someone's own choice as a fault.
        logger.info(
            "Dashboard on http://%s:%s — WEB_HOST was set explicitly.",
            WEB_HOST,
            WEB_PORT,
        )
    else:
        from utils.tailscale import ensure_serve

        logger.warning(
            "USE_TAILSCALE is set but this node's tailnet address could "
            "not be determined, so the dashboard bound to %s. Falling "
            "back to `tailscale serve`.",
            WEB_HOST,
        )
        if not await ensure_serve(WEB_PORT):
            logger.error(
                "Dashboard bound to %s; tailnet forwarding could not be "
                "confirmed, so it will NOT be reachable remotely. Check "
                "`tailscale status`, then restart Condor to pick up the "
                "tailnet address.",
                WEB_HOST,
            )
