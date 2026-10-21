"""Pooled API sockets must expire before the server's keep-alive closes them."""

import asyncio

import aiohttp
from hummingbot_api_client import HummingbotAPIClient

from condor.api_pool import POOL_KEEPALIVE_SECONDS, limit_pool_keepalive


def test_pooled_client_drops_idle_sockets_before_uvicorn_does():
    async def run():
        client = HummingbotAPIClient(
            base_url="http://127.0.0.1:9", username="u", password="p"
        )
        await client.init()
        try:
            connector = client._session.connector
            # The attribute this relies on; an aiohttp rename must fail here.
            assert isinstance(connector, aiohttp.BaseConnector)
            assert connector._keepalive_timeout == 15
            limit_pool_keepalive(client)
            assert connector._keepalive_timeout == POOL_KEEPALIVE_SECONDS < 5
        finally:
            await client.close()

    asyncio.run(run())
