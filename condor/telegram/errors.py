"""The one place that names the Telegram library's exceptions.

Everything else in the project catches and classifies through the names and
helpers here, so swapping the underlying library touches this module only.
The library-specific facts live here too: in aiogram every API failure is a
``TelegramAPIError`` whose ``str()`` starts with a label and ends with
Telegram's description; ``TelegramRetryAfter.retry_after`` is an ``int``;
``TelegramBadRequest`` is *not* a network error; and connection failures arrive
as ``TelegramNetworkError`` wrapping an ``aiohttp`` cause.

aiogram's constructors want the API method that failed. The ``*_error``
factories below build an exception from a description alone, for the transport
code and tests that raise one themselves.
"""

from __future__ import annotations

from dataclasses import dataclass

import aiohttp
from aiogram.exceptions import TelegramAPIError as TelegramFailure
from aiogram.exceptions import TelegramBadRequest as BadRequest
from aiogram.exceptions import TelegramForbiddenError as Forbidden
from aiogram.exceptions import TelegramNetworkError as NetworkFailure
from aiogram.exceptions import TelegramRetryAfter as RateLimited
from aiogram.exceptions import TelegramUnauthorizedError as Unauthorized

__all__ = [
    "BadRequest",
    "ConnectFailure",
    "Forbidden",
    "NetworkFailure",
    "OperationResult",
    "RateLimited",
    "TelegramFailure",
    "Unauthorized",
    "bad_request",
    "classify",
    "describe",
    "forbidden",
    "is_connect_failure",
    "is_not_modified",
    "network_failure",
    "rate_limited",
    "retry_delay",
]

#: Transport-level failures that happen before any bytes reach Telegram, so a
#: retry cannot duplicate a delivery. A connect timeout is one; a read or total
#: timeout is not, because the request may already be on its way.
ConnectFailure = (aiohttp.ClientConnectorError, aiohttp.ConnectionTimeoutError)


@dataclass(frozen=True)
class OperationResult:
    """What one send or edit came to: confirmed, failed (with a reason), or unknown."""

    status: str  # confirmed, failed, unknown
    message_id: int | None = None
    reason: str = ""


def bad_request(description: str, method=None) -> BadRequest:
    return BadRequest(method=method, message=description)


def forbidden(description: str, method=None) -> Forbidden:
    return Forbidden(method=method, message=description)


def rate_limited(seconds: float, method=None) -> RateLimited:
    return RateLimited(
        method=method, message="Too Many Requests", retry_after=int(seconds)
    )


def network_failure(
    description: str, *, cause: BaseException | None = None, method=None
) -> NetworkFailure:
    exc = NetworkFailure(method=method, message=description)
    if cause is not None:
        exc.__cause__ = cause
    return exc


def describe(exc: BaseException) -> str:
    """Telegram's own description of a failure, without the library's label.

    ``str()`` of an aiogram error reads "Telegram server says - <description>";
    user-facing results and the bell want only the description.
    """
    if isinstance(exc, TelegramFailure):
        return exc.message
    return str(exc)


def retry_delay(exc: BaseException) -> float:
    """Seconds Telegram asked us to wait, from a rate-limit error."""
    value = getattr(exc, "retry_after", 0)
    seconds = value.total_seconds() if hasattr(value, "total_seconds") else float(value)
    return max(0.0, seconds)


def is_not_modified(exc: BaseException) -> bool:
    """An edit that would leave the message as it is: harmless, not a failure."""
    return "not modified" in str(exc).lower()


def is_connect_failure(exc: BaseException) -> bool:
    """A failure to connect at all, following the library's cause chain."""
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, ConnectFailure):
            return True
        if not isinstance(cause, NetworkFailure):
            break
        cause = cause.__cause__
    return False


def classify(
    exc: BaseException, *, operation: str = "send", parse_mode: str | None = None
) -> OperationResult:
    """Read a delivery exception as an outcome.

    ``confirmed`` is only ever returned for an edit that changed nothing; the
    caller fills in the message id it already knows. ``unknown`` means the
    request may have reached Telegram, so the caller must not blindly resend.
    """
    if isinstance(exc, RateLimited):
        return OperationResult("failed", reason="rate_limit")
    if isinstance(exc, BadRequest):
        error = str(exc).lower()
        if operation == "edit" and is_not_modified(exc):
            return OperationResult("confirmed")
        if parse_mode and ("parse" in error or "entit" in error):
            return OperationResult("failed", reason="parse")
        if operation == "edit" and (
            "not found" in error
            or "can't be edited" in error
            or "cannot be edited" in error
        ):
            return OperationResult("failed", reason="missing")
        return OperationResult("failed", reason="rejected")
    if isinstance(exc, Forbidden):
        return OperationResult("failed", reason="forbidden")
    if isinstance(exc, NetworkFailure) or isinstance(exc, ConnectFailure):
        if is_connect_failure(exc):
            return OperationResult("failed", reason="connect")
        return OperationResult("unknown")
    # Read/write failures can follow a successful send.
    return OperationResult("unknown")
