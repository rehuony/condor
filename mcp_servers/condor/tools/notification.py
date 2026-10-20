"""Announce something to the user.

Goes through the main process (``POST /agents/notify``), which checks permissions
and records the notification in its originating conversation when one exists
(ARCH-088). Text-only calls can fall back to direct Telegram delivery after a
confirmed connection failure. Images always need the main process for file
authorization and attachment storage; ambiguous failures are never replayed.
"""

from aiohttp import ClientConnectorError

from mcp_servers.condor.condor_client import call_main_api
from mcp_servers.condor.settings import settings


async def send_notification(
    text: str = "", parse_mode: str = "Markdown", image_path: str = ""
) -> dict:
    """Send text and/or a server-local PNG/JPEG to the user.

    Returns:
        ``sent`` means all requested Telegram delivery succeeded. ``recorded``
        means the dashboard saved the notice, independently. Image calls also
        report ``image_sent``, ``text_sent`` and ``image_recorded``. Only an
        originating conversation can retain the image, including in local mode
        without Telegram. Inspect ``error`` and ``image_error`` for failures.
    """
    try:
        result = await call_main_api(
            "POST",
            "/agents/notify",
            {
                "text": text,
                "parse_mode": parse_mode,
                "chat_id": settings.chat_id,
                # Provenance: the route resolves this to the conversation the
                # announcement came from, so the chat keeps a trace of it.
                "session_key": settings.session_key,
                **({"image_path": image_path} if image_path else {}),
            },
            timeout=60,
        )
    except Exception as exc:
        # Only a failed connection proves the main process never received the
        # request. A timeout, rejection or lost response must not replay pages
        # that may already be on Telegram, or bypass a rejected request.
        if not image_path and isinstance(exc.__cause__, ClientConnectorError):
            return await _send_direct(text, parse_mode)
        return {
            "sent": False,
            "error": f"Main process did not confirm delivery; notification was not replayed: {exc}",
        }

    # A dashboard record is not a Telegram receipt. Preserve both outcomes,
    # including a failed or partial Telegram delivery. Replaying the whole
    # notification here would duplicate pages the main process already sent.
    if isinstance(result, dict) and ("sent" in result or "recorded" in result):
        return result

    return {
        "sent": False,
        "error": "Main process returned no delivery result; notification was not replayed",
    }


async def _send_direct(text: str, parse_mode: str) -> dict:
    """Last-resort Telegram push, straight from this subprocess."""
    if not settings.bot_token:
        return {
            "sent": False,
            "recorded": False,
            "error": "TELEGRAM_BOT_TOKEN not configured",
        }
    if not settings.chat_id:
        return {
            "sent": False,
            "recorded": False,
            "error": "CONDOR_CHAT_ID not configured",
        }

    # Normal tool calls use the main API; keep Telegram out of every MCP
    # subprocess's startup import graph until this fallback is actually needed.
    from telegram import Bot

    from condor.telegram_text import send_text

    try:
        async with Bot(token=settings.bot_token) as bot:
            await send_text(bot, settings.chat_id, text, parse_mode=parse_mode)
        return {"sent": True, "recorded": False}
    except Exception as exc:
        return {"sent": False, "recorded": False, "error": str(exc)}
