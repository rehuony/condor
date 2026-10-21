"""Detect pasted secrets in agent input and notify about them."""

import logging

from condor.runtime import (
    secrets,
)
from condor.telegram.errors import NetworkFailure
from condor.telegram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from ._shared import (
    stored_chat_conversation,
)

log = logging.getLogger(__name__)


_SECRET_SEEN_KEY = "_secret_notice_seen"


_CERTAIN_NOTICE = (
    "I removed a recovery phrase from that message and deleted the message. "
    "It was not sent to the agent and it is not in the transcript.\n\n"
    "Import wallets with /gateway — that flow is the only one that should ever "
    "see a key.\n\n"
    "Telegram may retain copies of source messages. If that phrase holds "
    "funds, move them."
)


_AMBIGUOUS_NOTICE = {
    secrets.EVM_HEX64: (
        "Heads up: that message carried a 0x value 64 hex digits long. An EVM "
        "private key looks exactly like that — and so does a transaction hash, "
        "which is why it was passed through untouched."
    ),
    secrets.SOLANA_B58: (
        "Heads up: that message carried an 87–88 character base58 value. A "
        "Solana secret key looks exactly like that — and so does a transaction "
        "signature, which is why it was passed through untouched."
    ),
}


_AMBIGUOUS_TAIL = (
    "\n\nIf it was a key, treat it as exposed: it reached the agent and the "
    "transcript. I only say this once per conversation."
)


async def _handle_pasted_secrets(update, context, text: str, *, messages=None) -> None:
    """Delete what was eaten, warn once about what was not.

    Both branches run off ``secrets.scan`` on this surface's own copy of the
    text. Running the detector twice is free, and two calls to one pure
    function cannot disagree with each other the way two implementations would.
    """
    findings = secrets.scan(text)
    if not findings:
        return

    if any(finding.certain for finding in findings):
        # Mirrors handlers/config/gateway/wallets.py: the message goes first,
        # and its failure is not the user's problem to hear about.
        source_messages = messages or (update.message,)
        # An external reply exposes content but may not expose a deletable
        # original. Never claim all copies were removed in that case.
        deleted = not any(
            getattr(message, "external_reply", None) for message in source_messages
        )
        for message in source_messages:
            try:
                await message.delete()
            except Exception:
                deleted = False
                log.debug("Could not delete a message carrying key material")
        notice = (
            _CERTAIN_NOTICE
            if deleted
            else _CERTAIN_NOTICE.replace(
                "and deleted the message", "but could not delete all source messages"
            )
        )
        await context.bot.send_message(chat_id=update.effective_chat.id, text=notice)

    from condor.preferences import secret_notices_enabled

    if not secret_notices_enabled(context.user_data):
        return

    # Per conversation, so a fresh chat is told again, and per kind, so being
    # warned about a hash does not use up the warning about a keypair. The flag
    # is the kind string — never the value, and never a hash of one.
    conv_id = stored_chat_conversation(context.user_data)
    seen = context.chat_data.setdefault(_SECRET_SEEN_KEY, {})
    told = seen.setdefault(conv_id, [])
    for kind in dict.fromkeys(
        finding.kind for finding in findings if not finding.certain
    ):
        if kind in told or kind not in _AMBIGUOUS_NOTICE:
            continue
        told.append(kind)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=_AMBIGUOUS_NOTICE[kind] + _AMBIGUOUS_TAIL,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Stop Warning Me",
                            callback_data="agent:secret_notices_off",
                        )
                    ]
                ]
            ),
        )


async def _notice_input_secrets(update, context, text: str, messages=None) -> None:
    try:
        await _handle_pasted_secrets(update, context, text, messages=messages)
    except NetworkFailure as exc:
        log.warning(
            "Telegram delivery failed stage=secret_notice update_id=%s error_type=%s",
            getattr(update, "update_id", None),
            type(exc).__name__,
        )
