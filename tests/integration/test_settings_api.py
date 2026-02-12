from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_settings_api_get_and_update(async_client):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["totpRequiredOnLogin"] is False
    assert payload["totpConfigured"] is False

    response = await async_client.put(
        "/api/settings",
        json={
            "totpRequiredOnLogin": False,
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["totpRequiredOnLogin"] is False
    assert updated["totpConfigured"] is False

    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["totpRequiredOnLogin"] is False
    assert payload["totpConfigured"] is False
