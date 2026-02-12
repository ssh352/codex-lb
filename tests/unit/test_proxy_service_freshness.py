from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import Account, AccountStatus
from app.modules.proxy.service import ProxyService


async def test_proxy_service_skips_freshness_repo_when_not_needed() -> None:
    service = ProxyService(repo_factory=lambda: (_ for _ in ()).throw(RuntimeError("repo_factory should not run")))

    ensure_calls = 0

    async def fake_ensure_fresh(account: Account, *, force: bool = False) -> Account:
        nonlocal ensure_calls
        ensure_calls += 1
        return account

    service._ensure_fresh = fake_ensure_fresh  # type: ignore[method-assign]

    account = Account(
        id="acc",
        chatgpt_account_id="chatgpt",
        email="a@example.com",
        plan_type="plus",
        access_token_encrypted=b"a",
        refresh_token_encrypted=b"b",
        id_token_encrypted=b"c",
        last_refresh=datetime.now(timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )

    out = await service._ensure_fresh_if_needed(account)
    assert out is account
    assert ensure_calls == 0


async def test_proxy_service_ensures_freshness_when_missing_account_id() -> None:
    service = ProxyService(repo_factory=lambda: (_ for _ in ()).throw(RuntimeError("repo_factory should not run")))

    ensure_calls = 0

    async def fake_ensure_fresh(account: Account, *, force: bool = False) -> Account:
        nonlocal ensure_calls
        ensure_calls += 1
        return account

    service._ensure_fresh = fake_ensure_fresh  # type: ignore[method-assign]

    account = Account(
        id="acc",
        chatgpt_account_id=None,
        email="a@example.com",
        plan_type="plus",
        access_token_encrypted=b"a",
        refresh_token_encrypted=b"b",
        id_token_encrypted=b"c",
        last_refresh=datetime.now(timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )

    out = await service._ensure_fresh_if_needed(account)
    assert out is account
    assert ensure_calls == 1
