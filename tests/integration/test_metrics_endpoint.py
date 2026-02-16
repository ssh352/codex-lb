from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import to_epoch_seconds_assuming_utc, utcnow
from app.db.models import Account, AccountStatus
from app.db.session import AccountsSessionLocal, SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


def _make_account(account_id: str, email: str, plan_type: str = "plus") -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


@pytest.mark.asyncio
async def test_metrics_endpoint_exports_core_metrics(async_client, db_setup) -> None:
    now = utcnow().replace(microsecond=0)
    secondary_reset_at = to_epoch_seconds_assuming_utc(now + timedelta(days=2))

    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(_make_account("acc_metrics", "metrics@example.com"))

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            "acc_metrics",
            50.0,
            window="secondary",
            recorded_at=now - timedelta(minutes=1),
            reset_at=secondary_reset_at,
            window_minutes=10080,
        )

    # Triggers a metrics refresh for per-account gauges.
    overview = await async_client.get("/api/dashboard/overview?requestLimit=1&requestOffset=0")
    assert overview.status_code == 200

    response = await async_client.get("/metrics")
    assert response.status_code == 200
    body = response.text
    assert "codex_lb_proxy_requests_total" in body
    assert "codex_lb_accounts_total" in body
    assert 'codex_lb_account_identity{account_id="acc_metrics",display="metrics@example.com"}' in body
    assert 'codex_lb_secondary_used_percent{account_id="acc_metrics"}' in body
