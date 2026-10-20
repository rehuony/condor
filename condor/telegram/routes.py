"""The rebuildable table the fixed dispatcher handlers look targets up in.

The dispatcher registers a handful of trampolines once (see
:mod:`condor.telegram.app`); what they call is read from the current
:class:`RouteTable` at dispatch time. ``main.register_handlers`` builds a new
table from freshly imported handler modules and installs it with
:func:`set_routes`, which is all a hot reload needs: no router to re-include,
no handler object holding a stale function.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

Handler = Callable[[Any, Any], Awaitable[Any]]


@dataclass
class RouteTable:
    """Where each kind of update goes, in registration order."""

    commands: dict[str, Handler] = field(default_factory=dict)
    #: ``(pattern, handler)`` tried in order against ``callback_query.data``;
    #: the first match wins, as it did when each pattern was its own handler.
    callbacks: list[tuple[re.Pattern[str], Handler]] = field(default_factory=list)
    message: Handler | None = None
    stopped_generation: Handler | None = None

    def command(self, name: str, handler: Handler) -> None:
        self.commands[name.lower()] = handler

    def callback(self, pattern: str, handler: Handler) -> None:
        self.callbacks.append((re.compile(pattern), handler))

    def callback_for(self, data: str | None) -> Handler | None:
        if data is None:
            return None
        for pattern, handler in self.callbacks:
            if pattern.search(data):
                return handler
        return None


_routes = RouteTable()


def get_routes() -> RouteTable:
    return _routes


def set_routes(table: RouteTable) -> None:
    global _routes
    _routes = table
