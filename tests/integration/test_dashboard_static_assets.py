from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.integration


_VERSION_RE = re.compile(r"/dashboard/index\.css\?v=([0-9a-f]{12})")


@pytest.mark.asyncio
async def test_dashboard_html_injects_consistent_asset_version(async_client) -> None:
    response = await async_client.get("/dashboard/", follow_redirects=True)
    assert response.status_code == 200
    assert "no-cache" in response.headers.get("cache-control", "").lower()

    html = response.text
    assert "__ASSET_VERSION__" not in html

    match = _VERSION_RE.search(html)
    assert match is not None
    version = match.group(1)

    for asset in (
        "index.css",
        "selection_utils.js",
        "ui_utils.js",
        "state_defaults.js",
        "sort_utils.js",
        "index.js",
    ):
        assert f"/dashboard/{asset}?v={version}" in html

    assert 'class="account-id-short"' in html
    assert "accounts-id-col" in html


@pytest.mark.asyncio
async def test_spa_routes_share_dashboard_assets(async_client) -> None:
    dashboard = (await async_client.get("/dashboard/", follow_redirects=True)).text
    accounts = (await async_client.get("/accounts", follow_redirects=True)).text
    settings = (await async_client.get("/settings", follow_redirects=True)).text

    assert "__ASSET_VERSION__" not in accounts
    assert "__ASSET_VERSION__" not in settings
    assert dashboard == accounts
    assert dashboard == settings
