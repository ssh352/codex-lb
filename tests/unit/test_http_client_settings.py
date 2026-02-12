from __future__ import annotations

import aiohttp

from app.core.clients.http import close_http_client, init_http_client
from app.core.config.settings import get_settings


async def test_http_client_connector_settings_apply(monkeypatch) -> None:
    await close_http_client()

    monkeypatch.setenv("CODEX_LB_HTTP_CLIENT_CONNECTOR_LIMIT", "7")
    monkeypatch.setenv("CODEX_LB_HTTP_CLIENT_CONNECTOR_LIMIT_PER_HOST", "3")
    monkeypatch.setenv("CODEX_LB_HTTP_CLIENT_KEEPALIVE_TIMEOUT_SECONDS", "12.5")

    get_settings.cache_clear()
    client = await init_http_client()
    try:
        connector = client.session.connector
        assert isinstance(connector, aiohttp.TCPConnector)
        assert connector.limit == 7
        assert connector.limit_per_host == 3
        assert getattr(connector, "_keepalive_timeout", None) == 12.5
    finally:
        await close_http_client()
