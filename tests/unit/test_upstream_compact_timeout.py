from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import aiohttp
import pytest

from app.core.openai.requests import ResponsesCompactRequest


def test_settings_upstream_compact_timeout_seconds_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config.settings import get_settings

    monkeypatch.setenv("CODEX_LB_UPSTREAM_COMPACT_TIMEOUT_SECONDS", "123")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.upstream_compact_timeout_seconds == 123.0
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_compact_responses_uses_upstream_compact_timeout_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.clients import proxy as proxy_client

    @dataclass(frozen=True, slots=True)
    class FakeSettings:
        upstream_base_url: str = "http://upstream.invalid/backend-api"
        upstream_connect_timeout_seconds: float = 30.0
        upstream_compact_timeout_seconds: float = 123.0
        image_inline_fetch_enabled: bool = False

    monkeypatch.setattr(proxy_client, "get_settings", lambda: FakeSettings())

    class FakeResponse:
        status = 200

        async def json(self, *, content_type: str | None = None) -> dict:
            return {}

        async def __aenter__(self) -> "FakeResponse":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    class FakeSession:
        def __init__(self) -> None:
            self.seen_timeout: aiohttp.ClientTimeout | None = None

        def post(
            self,
            url: str,
            *,
            json: object,
            headers: object,
            timeout: aiohttp.ClientTimeout,
        ) -> FakeResponse:
            self.seen_timeout = timeout
            return FakeResponse()

    fake_session = FakeSession()
    payload = ResponsesCompactRequest(model="gpt-5.1", instructions="hi", input="ping")

    _ = await proxy_client.compact_responses(
        payload,
        headers={},
        access_token="token",
        account_id=None,
        session=cast(aiohttp.ClientSession, fake_session),
    )

    assert fake_session.seen_timeout is not None
    assert fake_session.seen_timeout.total == 123.0
    assert fake_session.seen_timeout.sock_read == 123.0
