from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator, cast

import pytest

from app.core.balancer.types import UpstreamError
from app.core.clients.proxy import ProxyResponseError
from app.core.openai.requests import ResponsesRequest
from app.db.models import Account, AccountStatus
from app.modules.proxy.load_balancer import AccountSelection, LoadBalancer
from app.modules.proxy.service import ProxyService


@dataclass
class _SelectCall:
    sticky_key: str | None
    reallocate_sticky: bool


class _FakeLoadBalancer(LoadBalancer):
    def __init__(self, accounts: list[Account]) -> None:
        self._accounts = accounts
        self._index = 0
        self.select_calls: list[_SelectCall] = []
        self.mark_rate_limit_calls: list[str] = []
        self.mark_usage_limit_reached_calls: list[str] = []

    def invalidate_snapshot(self) -> None:
        return

    async def select_account(
        self,
        sticky_key: str | None = None,
        *,
        reallocate_sticky: bool = False,
    ) -> AccountSelection:
        self.select_calls.append(_SelectCall(sticky_key=sticky_key, reallocate_sticky=reallocate_sticky))
        if self._index >= len(self._accounts):
            return AccountSelection(account=None, error_message="No accounts")
        account = self._accounts[self._index]
        self._index += 1
        return AccountSelection(account=account, error_message=None)

    async def mark_rate_limit(self, account: Account, error: UpstreamError) -> None:
        del error
        self.mark_rate_limit_calls.append(account.id)

    async def mark_usage_limit_reached(self, account: Account, error: UpstreamError) -> None:
        del error
        self.mark_usage_limit_reached_calls.append(account.id)

    async def mark_quota_exceeded(self, account: Account, error: UpstreamError) -> None:
        del error
        raise AssertionError(f"Unexpected quota_exceeded for account_id={account.id}")

    async def mark_permanent_failure(self, account: Account, error_code: str) -> None:
        del error_code
        raise AssertionError(f"Unexpected permanent_failure for account_id={account.id}")

    async def record_error(self, account: Account) -> None:
        raise AssertionError(f"Unexpected record_error for account_id={account.id}")


def _account(account_id: str) -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id="chatgpt",
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=b"a",
        refresh_token_encrypted=b"b",
        id_token_encrypted=b"c",
        last_refresh=datetime.now(timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _payload(*, sticky_key: str = "k") -> ResponsesRequest:
    return ResponsesRequest(
        model="gpt-4.1-mini",
        instructions="hi",
        input="hello",
        prompt_cache_key=sticky_key,
    )


def _usage_limit_reached_error() -> ProxyResponseError:
    return ProxyResponseError(
        429,
        {
            "error": {
                "message": "The usage limit has been reached",
                "type": "rate_limit_error",
                "code": "usage_limit_reached",
                "resets_at": 123,
            }
        },
    )


async def test_streaming_retries_across_accounts_on_retryable_http_error() -> None:
    service = ProxyService(repo_factory=lambda: (_ for _ in ()).throw(RuntimeError("repo_factory should not run")))
    service._load_balancer = _FakeLoadBalancer([_account("acc1"), _account("acc2")])

    async def fake_ensure_fresh_if_needed(account: Account) -> Account:
        return account

    service._ensure_fresh_if_needed = fake_ensure_fresh_if_needed  # type: ignore[method-assign]

    async def fake_stream_once(
        account: Account,
        _: ResponsesRequest,
        __: dict[str, str],
        ___: str,
        ____: bool,
        *,
        prompt_cache_key_hash: str | None,
        api: str,
        suppress_text_done_events: bool,
    ) -> AsyncIterator[str]:
        del prompt_cache_key_hash
        del suppress_text_done_events
        if account.id == "acc1":
            raise _usage_limit_reached_error()
        yield "data: ok\n\n"

    service._stream_once = fake_stream_once  # type: ignore[method-assign]

    lines = [
        line
        async for line in service._stream_with_retry(
            _payload(),
            {},
            forced_account_id=None,
            propagate_http_errors=False,
            api="responses",
            suppress_text_done_events=False,
        )
    ]
    assert lines == ["data: ok\n\n"]

    lb = cast(_FakeLoadBalancer, service._load_balancer)
    assert [call.reallocate_sticky for call in lb.select_calls] == [False, True]
    assert lb.mark_usage_limit_reached_calls == ["acc1"]


async def test_streaming_does_not_retry_after_emitting_output() -> None:
    service = ProxyService(repo_factory=lambda: (_ for _ in ()).throw(RuntimeError("repo_factory should not run")))
    service._load_balancer = _FakeLoadBalancer([_account("acc1"), _account("acc2")])

    async def fake_ensure_fresh_if_needed(account: Account) -> Account:
        return account

    service._ensure_fresh_if_needed = fake_ensure_fresh_if_needed  # type: ignore[method-assign]

    async def fake_stream_once(
        account: Account,
        _: ResponsesRequest,
        __: dict[str, str],
        ___: str,
        ____: bool,
        *,
        prompt_cache_key_hash: str | None,
        api: str,
        suppress_text_done_events: bool,
    ) -> AsyncIterator[str]:
        del prompt_cache_key_hash
        del suppress_text_done_events
        yield "data: chunk\n\n"
        raise _usage_limit_reached_error()

    service._stream_once = fake_stream_once  # type: ignore[method-assign]

    lines = [
        line
        async for line in service._stream_with_retry(
            _payload(),
            {},
            forced_account_id=None,
            propagate_http_errors=False,
            api="responses",
            suppress_text_done_events=False,
        )
    ]
    assert lines[0] == "data: chunk\n\n"
    assert any("event: response.failed" in line for line in lines[1:])

    lb = cast(_FakeLoadBalancer, service._load_balancer)
    assert [call.reallocate_sticky for call in lb.select_calls] == [False]
    assert lb.mark_usage_limit_reached_calls == ["acc1"]


async def test_streaming_propagates_retryable_http_error_when_no_failover_accounts() -> None:
    service = ProxyService(repo_factory=lambda: (_ for _ in ()).throw(RuntimeError("repo_factory should not run")))
    service._load_balancer = _FakeLoadBalancer([_account("acc1")])

    async def fake_ensure_fresh_if_needed(account: Account) -> Account:
        return account

    service._ensure_fresh_if_needed = fake_ensure_fresh_if_needed  # type: ignore[method-assign]

    async def fake_stream_once(
        account: Account,
        _: ResponsesRequest,
        __: dict[str, str],
        ___: str,
        ____: bool,
        *,
        prompt_cache_key_hash: str | None,
        api: str,
        suppress_text_done_events: bool,
    ) -> AsyncIterator[str]:
        del prompt_cache_key_hash
        del suppress_text_done_events
        raise _usage_limit_reached_error()
        if False:
            yield "unreachable"

    service._stream_once = fake_stream_once  # type: ignore[method-assign]

    gen = service._stream_with_retry(
        _payload(),
        {},
        forced_account_id=None,
        propagate_http_errors=True,
        api="responses",
        suppress_text_done_events=False,
    )
    with pytest.raises(ProxyResponseError) as exc_info:
        await gen.__anext__()
    assert exc_info.value.status_code == 429
