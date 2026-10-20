"""Three handler defects that hid behind broad ``except`` clauses (WP-A5).

* ``get_token_cache_from_gateway`` referenced a ``context`` it never received,
  so the ``NameError`` was swallowed and every lookup fell back to the static
  token table.
* The DEX and CEX menus parked an ``asyncio.Task`` in ``user_data``, which the
  persistence snapshot then tried to pickle.
* The continuous-routine context assigned into PTB's read-only
  ``application.user_data`` proxy for a user without a bucket.
"""

import asyncio
import pickle
from collections import defaultdict
from types import MappingProxyType, SimpleNamespace

import pytest

from handlers.cex import menu as cex_menu
from handlers.dex import menu as dex_menu
from handlers.dex.pools import _common as pools
from handlers.routines import _user_bucket


def test_token_cache_lookup_reaches_gateway_with_the_handler_context(monkeypatch):
    seen = {}

    class Gateway:
        async def get_network_tokens(self, network):
            return {"tokens": [{"address": "So1", "symbol": "SOL"}]}

    async def fake_get_client(chat_id, user_id=None, *, user_data=None, server=None):
        seen["chat_id"], seen["user_data"] = chat_id, user_data
        return SimpleNamespace(gateway=Gateway())

    monkeypatch.setattr(pools, "get_client", fake_get_client)
    context = SimpleNamespace(user_data={"_user_id": 7})

    cache = asyncio.run(pools.get_token_cache_from_gateway(chat_id=7, context=context))

    assert seen == {"chat_id": 7, "user_data": context.user_data}
    assert cache["So1"] == "SOL"


@pytest.mark.parametrize(
    "module, cancel",
    [
        (dex_menu, dex_menu.cancel_dex_loading_task),
        (cex_menu, cex_menu.cancel_cex_loading_task),
    ],
)
def test_menu_loading_task_stays_out_of_user_data(module, cancel):
    async def run():
        user_data = {"_user_id": 7}
        context = SimpleNamespace(user_data=user_data)
        task = asyncio.create_task(asyncio.sleep(10))
        module._loading_tasks[7] = task

        pickle.dumps(user_data)  # nothing unpicklable was parked here
        cancel(context)
        await asyncio.sleep(0)
        assert task.cancelled()
        assert 7 not in module._loading_tasks
        cancel(context)  # idempotent

    asyncio.run(run())


def test_user_bucket_mints_through_ptb_read_only_proxy():
    backing = defaultdict(dict)
    application = SimpleNamespace(user_data=MappingProxyType(backing))

    bucket = _user_bucket(application, 42)
    bucket["x"] = 1

    assert backing[42] == {"x": 1}
    assert _user_bucket(application, 42) is bucket


def test_user_bucket_accepts_a_plain_dict():
    application = SimpleNamespace(user_data={})

    bucket = _user_bucket(application, 42)

    assert application.user_data[42] is bucket
