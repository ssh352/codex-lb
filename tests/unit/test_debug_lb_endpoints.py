from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config.settings import get_settings
from app.main import create_app


@pytest.mark.asyncio
async def test_debug_endpoints_disabled_return_404(async_client) -> None:
    resp = await async_client.get("/debug/lb/state")
    assert resp.status_code == 404
    resp = await async_client.get("/debug/lb/events")
    assert resp.status_code == 404


@pytest_asyncio.fixture
async def debug_async_client(db_setup, monkeypatch):  # noqa: ARG001
    monkeypatch.setenv("CODEX_LB_DEBUG_ENDPOINTS_ENABLED", "true")
    get_settings.cache_clear()
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


@pytest.mark.asyncio
async def test_debug_endpoints_enabled_return_payloads(debug_async_client) -> None:
    state = await debug_async_client.get("/debug/lb/state")
    assert state.status_code == 200
    payload = state.json()
    assert "server_time" in payload
    assert "snapshot_updated_at" in payload
    assert "sticky_backend" in payload
    assert "pinned_accounts" in payload
    assert "accounts" in payload

    events = await debug_async_client.get("/debug/lb/events?limit=1")
    assert events.status_code == 200
    payload = events.json()
    assert "server_time" in payload
    assert "events" in payload
    assert isinstance(payload["events"], list)
