"""``/web``: a one-time login link for the web dashboard."""

from urllib.parse import urlparse

from condor.telegram.errors import BadRequest
from condor.telegram.types import (
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from utils.auth import restricted
from utils.config import WEB_URL


def _dashboard_button_url(url: str) -> str | None:
    """The dashboard link Telegram will accept in an inline URL button, if any.

    Telegram rejects ``localhost`` outright ("Wrong HTTP URL") but accepts the
    loopback IP, so the local default (``http://localhost:8088``) still gets a
    button by swapping the host — which is what a Telegram client running on
    the same machine needs anyway. Any other dotless hostname (a bare LAN name)
    has no such equivalent, so those keep the copy-paste link only.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host == "localhost":
        parsed = parsed._replace(netloc=parsed.netloc.replace(host, "127.0.0.1", 1))
        return parsed.geturl()
    if "." not in host:
        return None
    return url


@restricted
async def web_command(update: Update, context: Context) -> None:
    """Generate a one-time login link for the web dashboard."""
    from condor.web.auth import create_login_token

    user = update.effective_user
    token = create_login_token(user.id, user.username or "", user.first_name or "")

    url = f"{WEB_URL}/login?token={token}"
    _hostname = urlparse(WEB_URL).hostname or ""
    is_localhost = (
        "localhost" in WEB_URL or "127.0.0.1" in WEB_URL or "." not in _hostname
    )

    button_url = _dashboard_button_url(url)
    keyboard = (
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🌐 Open Dashboard", url=button_url)]
            ]
        )
        if button_url
        else None
    )

    if is_localhost:
        # A loopback button only opens on the machine running the bot, so the
        # raw link stays visible for a phone on the same network to adapt.
        text = (
            f"🌐 *Web Dashboard*\n\n"
            f"Open this link in your browser:\n`{url}`\n\n"
            f"_Link valid for 5 minutes\\._"
        )
    else:
        text = (
            "🌐 *Web Dashboard*\n\n"
            "Tap the button below to open the dashboard\\.\n"
            "_Link valid for 5 minutes\\._"
        )

    try:
        await update.message.answer(
            text, reply_markup=keyboard, parse_mode="MarkdownV2"
        )
    except BadRequest:
        # Telegram refuses URL buttons it can't parse (host without a public
        # TLD, an unusual scheme). Losing the button must not lose the link.
        if keyboard is None:
            raise
        await update.message.answer(
            f"🌐 *Web Dashboard*\n\n"
            f"Open this link in your browser:\n`{url}`\n\n"
            f"_Link valid for 5 minutes\\._",
            parse_mode="MarkdownV2",
        )
