"""condor.telegram.errors is the only place that reads the library's exceptions.

Everything downstream sees an ``OperationResult`` or a boolean, so swapping the
Telegram library later touches this module and nothing else.
"""

import asyncio
from datetime import timedelta

import aiohttp
import pytest

from condor.telegram import errors
from condor.telegram.errors import (
    bad_request,
    forbidden,
    network_failure,
    rate_limited,
)
from condor.telegram.transport import message_id_of, reached_telegram


def test_retry_delay_accepts_timedelta_and_numbers():
    assert errors.retry_delay(rate_limited(3)) == 3.0

    class _Exc(Exception):
        retry_after = timedelta(seconds=7)

    assert errors.retry_delay(_Exc()) == 7.0

    class _Negative(Exception):
        retry_after = -1

    assert errors.retry_delay(_Negative()) == 0


def test_is_not_modified_matches_telegram_wording():
    assert errors.is_not_modified(bad_request("Message is not modified: nothing"))
    assert errors.is_not_modified(RuntimeError("MESSAGE IS NOT MODIFIED"))
    assert not errors.is_not_modified(bad_request("Message to edit not found"))


@pytest.mark.parametrize(
    "exc, operation, parse_mode, status, reason",
    [
        (rate_limited(1), "send", None, "failed", "rate_limit"),
        (bad_request("Message is not modified"), "edit", None, "confirmed", ""),
        (bad_request("Can't parse entities"), "send", "MarkdownV2", "failed", "parse"),
        (bad_request("Message to edit not found"), "edit", None, "failed", "missing"),
        (bad_request("Message can't be deleted"), "delete", None, "failed", "rejected"),
        (forbidden("bot was blocked"), "send", None, "failed", "forbidden"),
        (network_failure("boom"), "send", None, "unknown", ""),
        (ValueError("nope"), "send", None, "unknown", ""),
    ],
)
def test_classify_buckets(exc, operation, parse_mode, status, reason):
    result = errors.classify(exc, operation=operation, parse_mode=parse_mode)
    assert (result.status, result.reason) == (status, reason)


def test_classify_sees_connect_failures_through_library_wrapping():
    inner = aiohttp.ClientConnectorError(
        aiohttp.client_reqrep.ConnectionKey("h", 443, True, True, None, None, None),
        OSError("refused"),
    )
    outer = network_failure("wrapped", cause=inner)
    assert errors.is_connect_failure(outer)
    assert errors.classify(outer).reason == "connect"
    connect_timeout = aiohttp.ConnectionTimeoutError("connect timed out")
    assert (
        errors.classify(network_failure("timeout", cause=connect_timeout)).reason
        == "connect"
    )
    # A failure after the request left is not a connection failure.
    assert errors.classify(network_failure("response lost")).status == "unknown"
    read_timeout = aiohttp.SocketTimeoutError("read timed out")
    assert (
        errors.classify(network_failure("timeout", cause=read_timeout)).status
        == "unknown"
    )
    assert (
        errors.classify(network_failure("timeout", cause=asyncio.TimeoutError())).status
        == "unknown"
    )


def test_describe_strips_the_library_label():
    assert errors.describe(bad_request("chat not found")) == "chat not found"
    assert "Telegram server says" in str(bad_request("chat not found"))
    assert errors.describe(ValueError("nope")) == "nope"


def test_parse_error_needs_a_parse_mode():
    # Without a parse mode the entity complaint cannot be about our markup.
    result = errors.classify(bad_request("Can't parse entities"), parse_mode=None)
    assert result.reason == "rejected"


class _Message:
    def __init__(self, message_id):
        self.message_id = message_id


def test_message_id_of_reads_only_real_message_ids():
    assert message_id_of(_Message(7)) == 7
    assert message_id_of(_Message(0)) is None
    assert message_id_of(_Message(True)) is None
    assert message_id_of(True) is None
    assert message_id_of(None) is None


def test_reached_telegram_means_a_result_came_back():
    assert reached_telegram(_Message(7))
    assert reached_telegram(True)
    assert not reached_telegram(None)
