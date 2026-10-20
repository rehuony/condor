"""Table-driven callback routing shared by the handler packages.

Each package declares two tables: ``SIMPLE_ACTIONS`` maps a whole action
string to a ``(update, context)`` handler, and ``PREFIX_ACTIONS`` lists
``(prefix, handler, parse)`` rows for actions that carry a parameter after
the prefix. Rows are tried in order, so a longer prefix must precede any
shorter prefix it starts with (``cu_delok:`` before ``cu_del:``).
"""

from typing import Any, Awaitable, Callable, Mapping, Sequence

from condor.telegram.types import Context, Update

HandlerFunc = Callable[[Update, Context], Awaitable[Any]]
PrefixRoute = tuple[str, Callable[..., Awaitable[Any]], Callable[[str], Any]]


async def noop(update: Update, context: Context) -> None:
    """Handler for inert buttons such as page indicators."""


def none_if_all(value: str) -> str | None:
    """Parser for ``..._all`` buttons that mean "no filter"."""
    return None if value == "all" else value


async def dispatch_action(
    update: Update,
    context: Context,
    action: str,
    simple: Mapping[str, HandlerFunc],
    prefixes: Sequence[PrefixRoute] = (),
) -> bool:
    """Run the handler registered for ``action``.

    Returns False when neither table has a matching entry so the caller can
    fall through to its unknown-action reply.
    """
    handler = simple.get(action)
    if handler is not None:
        await handler(update, context)
        return True
    for prefix, prefixed_handler, parse in prefixes:
        if action.startswith(prefix):
            await prefixed_handler(update, context, parse(action[len(prefix) :]))
            return True
    return False
