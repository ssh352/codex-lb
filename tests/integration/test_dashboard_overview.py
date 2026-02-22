from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import to_epoch_seconds_assuming_utc, utcnow
from app.db.models import Account, AccountStatus
from app.db.session import AccountsSessionLocal, SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.settings.repository import SettingsRepository
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
async def test_dashboard_overview_combines_data(async_client, db_setup):
    now = utcnow().replace(microsecond=0)
    primary_time = now - timedelta(minutes=5)
    secondary_time = now - timedelta(minutes=2)
    secondary_reset_at = to_epoch_seconds_assuming_utc(now + timedelta(days=3, hours=12))

    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(_make_account("acc_dash", "dash@example.com"))

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        logs_repo = RequestLogsRepository(session)
        await usage_repo.add_entry(
            "acc_dash",
            20.0,
            window="primary",
            recorded_at=primary_time,
        )
        await usage_repo.add_entry(
            "acc_dash",
            50.0,
            window="secondary",
            recorded_at=secondary_time,
            reset_at=secondary_reset_at,
            window_minutes=10080,
        )
        await logs_repo.add_log(
            account_id="acc_dash",
            request_id="req_dash_1",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=50,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/overview?requestLimit=10&requestOffset=0")
    assert response.status_code == 200
    payload = response.json()

    assert payload["accounts"][0]["accountId"] == "acc_dash"
    assert payload["summary"]["primaryWindow"]["capacityCredits"] == pytest.approx(225.0)
    assert payload["windows"]["primary"]["windowKey"] == "primary"
    assert payload["windows"]["secondary"]["windowKey"] == "secondary"
    assert len(payload["requestLogs"]) == 1
    assert payload["lastSyncAt"] == secondary_time.isoformat() + "Z"
    assert payload["wastePacing"]["summary"]["accountsEvaluated"] == 1
    matched = next((entry for entry in payload["wastePacing"]["accounts"] if entry["accountId"] == "acc_dash"), None)
    assert matched is not None
    assert matched["onTrack"] is True
    assert matched["projectedWasteCredits"] == pytest.approx(0.0, abs=0.5)


@pytest.mark.asyncio
async def test_dashboard_overview_marks_pinned_accounts(async_client, db_setup):
    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(_make_account("acc_pin_dash", "pin_dash@example.com"))

    async with SessionLocal() as session:
        settings_repo = SettingsRepository(session)
        await settings_repo.update(pinned_account_ids=["acc_pin_dash"])

    response = await async_client.get("/api/dashboard/overview?requestLimit=10&requestOffset=0")
    assert response.status_code == 200
    payload = response.json()
    matched = next((account for account in payload["accounts"] if account["accountId"] == "acc_pin_dash"), None)
    assert matched is not None
    assert matched["pinned"] is True


@pytest.mark.asyncio
async def test_dashboard_overview_clears_stale_blocked_status(async_client, db_setup):
    encryptor = TokenEncryptor()
    account = Account(
        id="acc_stale_dash",
        email="stale_dash@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.RATE_LIMITED,
        deactivation_reason=None,
        reset_at=1,
    )

    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(account)

    response = await async_client.get("/api/dashboard/overview?requestLimit=1&requestOffset=0")
    assert response.status_code == 200
    payload = response.json()
    matched = next((item for item in payload["accounts"] if item["accountId"] == "acc_stale_dash"), None)
    assert matched is not None
    assert matched["status"] == "active"
    assert matched.get("statusResetAt") is None
