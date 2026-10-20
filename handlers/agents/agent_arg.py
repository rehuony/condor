"""Resolve the agent named in a /agent argument."""

import logging

from condor.telegram.types import (
    Context,
    Update,
)

from .talk_to import _raise_agent

log = logging.getLogger(__name__)


# What someone can type after /agent to mean the coordinator rather than a
# specialist, so the command can unbind a chat as well as bind it.
_CONDOR_ALIASES = {"condor", "coordinator", "default", "none", "-"}


# How many agents an unresolved argument lists back. A long roster belongs in
# the picker, which paginates; this is only meant to name the near misses.
_ARG_LIST_LIMIT = 10


def _normalize_agent_query(text: str) -> str:
    """Fold the ways one agent's name gets typed into one comparable form.

    Slugs are underscored and names are spaced and capitalised, so "Orca LP
    Expert", "orca_lp_expert" and "orca-lp-expert" all have to arrive at the
    same string before anything can be compared.
    """
    return "".join(c for c in text.lower() if c.isalnum())


def _resolve_agent_argument(arg: str) -> tuple[str, str, str]:
    """Resolve ``/agent <name>`` to ``(slug, label, error)``.

    Exact matches win over partial ones, so an agent whose name is contained in
    another's is still reachable by typing it in full. A partial match that hits
    more than one agent is refused rather than guessed: raising the wrong
    specialist swaps the identity, the tools, the pinned server and the memory
    scope at once, and the only thing on screen to catch it would be the name in
    the header. An empty slug with no error is the coordinator.
    """
    from condor.agents.agent import AgentStore

    if arg.strip().lower() in _CONDOR_ALIASES:
        return "", "Condor", ""

    needle = _normalize_agent_query(arg)
    if not needle:
        return "", "", "Tell me who to talk to, e.g. /agent orca_lp_expert."

    agents = AgentStore().list_specialists()
    if not agents:
        return (
            "",
            "",
            "No agents defined yet. Create one with /agent → Condor, or from "
            "the dashboard's Agents page.",
        )

    def _label(agent) -> str:
        return agent.name or agent.slug

    def _listing(rows) -> str:
        shown = "\n".join(f"• {_label(a)} ({a.slug})" for a in rows[:_ARG_LIST_LIMIT])
        rest = len(rows) - _ARG_LIST_LIMIT
        if rest > 0:
            shown += f"\n…and {rest} more — see /agent → Talk to."
        return shown

    keys = {
        a.slug: (_normalize_agent_query(a.slug), _normalize_agent_query(_label(a)))
        for a in agents
    }
    matches = [a for a in agents if needle in keys[a.slug]]
    if not matches:
        matches = [a for a in agents if any(needle in k for k in keys[a.slug])]

    if not matches:
        return "", "", f"No agent matches '{arg}'. You can talk to:\n{_listing(agents)}"
    if len(matches) > 1:
        return (
            "",
            "",
            f"'{arg}' matches more than one agent:\n{_listing(matches)}"
            "\n\nType the slug in full.",
        )
    return matches[0].slug, _label(matches[0]), ""


async def _handle_agent_argument(update: Update, context: Context, arg: str) -> None:
    """``/agent <name>`` — raise that specialist without going through the menu.

    The shortcut for the chats that always belong to the same desk: two taps and
    a page of picker collapse into the name you already know. ``/agent condor``
    is the way back to the coordinator.
    """
    slug, label, error = _resolve_agent_argument(arg)
    if error:
        await update.message.answer(error)
        return

    await _raise_agent(update, context, slug, label)
