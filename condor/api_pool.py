"""Keep pooled Hummingbot API connections inside the server's keep-alive window.

The API runs under uvicorn, which closes an idle keep-alive connection after
5 seconds; aiohttp keeps idle connections pooled for 15. A request written to a
socket the server has just closed fails with ``ServerDisconnectedError`` before
the API ever sees it, which surfaced as "Server disconnected" on screens opened
a few seconds after another one. Dropping idle sockets on our side first means
a request either reuses a live connection or opens a fresh one.
"""

from __future__ import annotations

from typing import Any

#: Below uvicorn's default ``--timeout-keep-alive`` of 5 seconds.
POOL_KEEPALIVE_SECONDS = 4.0


def limit_pool_keepalive(client: Any) -> None:
    """Shorten an initialized ``HummingbotAPIClient``'s idle-connection lifetime.

    hummingbot-api-client builds its aiohttp session inside ``init()`` with no
    connector argument, so the default connector's keep-alive is adjusted in
    place. aiohttp reads it on every reuse decision and when scheduling the
    idle cleanup, so changing it after construction is effective.
    """
    connector = client._session.connector
    connector._keepalive_timeout = POOL_KEEPALIVE_SECONDS
