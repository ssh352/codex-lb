from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

import pytest

from app.core.crypto import TokenEncryptor
from app.core.openai.models import OpenAIResponsePayload, ResponseUsage
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.request_logs.buffer import get_request_log_buffer
from app.core.utils.sse import format_sse_event
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.proxy.load_balancer import AccountSelection
from app.modules.proxy.service import ProxyService


def _enable_request_log_buffer(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_REQUEST_LOGS_BUFFER_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    get_request_log_buffer.cache_clear()


def _drain_request_log_buffer() -> None:
    buffer = get_request_log_buffer()
    buffer.drain(10_000)


def _account(account_id: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id="chatgpt",
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


@pytest.mark.asyncio
async def test_streaming_falls_back_codex_session_id_from_uuid_prompt_cache_key(monkeypatch) -> None:
    _enable_request_log_buffer(monkeypatch)
    _drain_request_log_buffer()

    session_id = "019c7f34-55eb-7512-b98f-76622d14cd68"

    from app.modules.proxy import service as proxy_service_mod

    async def fake_stream_responses(
        payload: ResponsesRequest,
        headers: dict[str, str],
        access_token: str,
        account_id: str | None,
        *,
        raise_for_status: bool,
    ) -> AsyncIterator[str]:
        del payload, headers, access_token, account_id, raise_for_status
        yield format_sse_event(
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_test",
                    "status": "completed",
                    "usage": {"input_tokens": 1, "output_tokens": 2},
                },
            }
        )

    monkeypatch.setattr(proxy_service_mod, "core_stream_responses", fake_stream_responses)

    service = ProxyService(repo_factory=lambda: (_ for _ in ()).throw(RuntimeError("repo_factory should not run")))
    account = _account("acc_stream")
    payload = ResponsesRequest(
        model="gpt-4.1-mini",
        instructions="hi",
        input="hello",
        prompt_cache_key=session_id,
    )

    lines = [
        line
        async for line in service._stream_once(
            account,
            payload,
            {},
            "req_stream",
            False,
            prompt_cache_key_hash=None,
            api="responses",
            suppress_text_done_events=False,
        )
    ]
    assert lines

    entries = get_request_log_buffer().drain(10_000)
    assert entries
    assert entries[-1].codex_session_id == session_id

    _drain_request_log_buffer()
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    get_request_log_buffer.cache_clear()


@pytest.mark.asyncio
async def test_compact_falls_back_codex_session_id_from_uuid_prompt_cache_key(monkeypatch) -> None:
    _enable_request_log_buffer(monkeypatch)
    _drain_request_log_buffer()

    session_id = "019c7f34-55eb-7512-b98f-76622d14cd68"

    from app.modules.proxy import service as proxy_service_mod

    async def fake_compact_responses(
        payload: ResponsesCompactRequest,
        headers: dict[str, str],
        access_token: str,
        account_id: str | None,
    ) -> OpenAIResponsePayload:
        del payload, headers, access_token, account_id
        return OpenAIResponsePayload(
            id="resp_compact_test",
            status="completed",
            usage=ResponseUsage(input_tokens=1, output_tokens=2, total_tokens=3),
        )

    monkeypatch.setattr(proxy_service_mod, "core_compact_responses", fake_compact_responses)

    service = ProxyService(repo_factory=lambda: (_ for _ in ()).throw(RuntimeError("repo_factory should not run")))

    @dataclass
    class _FakeLB:
        account: Account

        async def select_account(
            self,
            sticky_key: str | None = None,
            *,
            reallocate_sticky: bool = False,
        ) -> AccountSelection:
            del sticky_key, reallocate_sticky
            return AccountSelection(account=self.account, error_message=None)

    service._load_balancer = _FakeLB(_account("acc_compact"))  # type: ignore[assignment]

    async def fake_ensure_fresh_if_needed(account: Account) -> Account:
        return account

    service._ensure_fresh_if_needed = fake_ensure_fresh_if_needed  # type: ignore[method-assign]

    payload = ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-4.1-mini",
            "instructions": "hi",
            "input": "hello",
            "prompt_cache_key": session_id,
        }
    )

    result = await service.compact_responses(payload, {})
    assert result.status == "completed"

    entries = get_request_log_buffer().drain(10_000)
    assert entries
    assert entries[-1].codex_session_id == session_id

    _drain_request_log_buffer()
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    get_request_log_buffer.cache_clear()
