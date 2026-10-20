"""Routines use one server name for API requests and stored results."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import config_manager
from routines.base import RoutineContext, resolve_server_name
from tests.conftest import load_shared_routine


def test_launch_server_does_not_consult_ambient_defaults(monkeypatch):
    effective = Mock(side_effect=AssertionError("must not resolve the chat"))
    default = Mock(side_effect=AssertionError("must not load the default"))
    monkeypatch.setattr(config_manager, "get_effective_server", effective)
    monkeypatch.setattr(config_manager, "get_config_manager", default)
    context = RoutineContext.pinned("launched-on", chat_id=42, bot=None)

    assert resolve_server_name(context) == "launched-on"
    effective.assert_not_called()
    default.assert_not_called()


def test_telegram_resolution_uses_the_chat_and_owner_bucket(monkeypatch):
    context = RoutineContext(
        chat_id=-10042,
        owner_id=7,
        instance_id="run-1",
        user_data={"owner-preference": "chat-server"},
        bot=None,
    )
    effective = Mock(return_value="chat-server")
    default = Mock(side_effect=AssertionError("must not load the default"))
    monkeypatch.setattr(config_manager, "get_effective_server", effective)
    monkeypatch.setattr(config_manager, "get_config_manager", default)

    assert resolve_server_name(context) == "chat-server"
    effective.assert_called_once_with(-10042, context.user_data)
    default.assert_not_called()


@pytest.mark.parametrize(
    ("lookup_error", "default_server", "expected"),
    [
        (None, "default-server", "default-server"),
        (RuntimeError("preferences unavailable"), "default-server", "default-server"),
        (None, None, ""),
    ],
)
def test_missing_or_unavailable_chat_server_uses_the_default(
    monkeypatch, lookup_error, default_server, expected
):
    context = RoutineContext(
        chat_id=42, owner_id=7, instance_id="run-1", user_data={}, bot=None
    )
    monkeypatch.setattr(
        config_manager,
        "get_effective_server",
        Mock(return_value=None, side_effect=lookup_error),
    )
    monkeypatch.setattr(
        config_manager,
        "get_config_manager",
        lambda: SimpleNamespace(get_default_server=lambda: default_server),
    )

    assert resolve_server_name(context) == expected


@pytest.mark.parametrize(
    "routine",
    [load_shared_routine("archived_analyzer"), load_shared_routine("backtest_chart")],
    ids=["archived_analyzer", "backtest_chart"],
)
def test_report_routines_request_the_explicit_launch_server(monkeypatch, routine):
    get_client = AsyncMock(return_value=None)
    monkeypatch.setattr(routine, "get_client", get_client)
    context = RoutineContext.pinned("launched-on", bot=None)

    asyncio.run(routine.run(routine.Config(), context))

    get_client.assert_awaited_once_with(
        context.chat_id, user_data=context.user_data, server="launched-on"
    )
