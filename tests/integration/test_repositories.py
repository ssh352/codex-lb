from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import AccountsSessionLocal, SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


def _make_account(account_id: str, email: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


@pytest.mark.asyncio
async def test_accounts_upsert_updates_existing_by_email(db_setup):
    async with AccountsSessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_make_account("acc1", "dup@example.com"))

        updated = _make_account("acc2", "dup@example.com")
        updated.plan_type = "team"
        updated.status = AccountStatus.PAUSED
        updated.deactivation_reason = "reauth"
        await repo.upsert(updated)

        result = await session.execute(select(Account).where(Account.email == "dup@example.com"))
        stored = result.scalar_one()
        assert stored.id == "acc1"
        assert stored.plan_type == "team"
        assert stored.status == AccountStatus.PAUSED
        assert stored.deactivation_reason == "reauth"

        all_accounts = await session.execute(select(Account))
        assert len(list(all_accounts.scalars().all())) == 1


@pytest.mark.asyncio
async def test_usage_repository_aggregate(db_setup):
    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(_make_account("acc1", "acc1@example.com"))
        await accounts_repo.upsert(_make_account("acc2", "acc2@example.com"))
    async with SessionLocal() as session:
        repo = UsageRepository(session)
        now = utcnow()
        await repo.add_entry("acc1", 10.0, recorded_at=now - timedelta(hours=1))
        await repo.add_entry("acc1", 30.0, recorded_at=now - timedelta(minutes=30))
        await repo.add_entry("acc2", 50.0, recorded_at=now - timedelta(minutes=10))

        rows = await repo.aggregate_since(now - timedelta(hours=5))
        row_map = {row.account_id: row for row in rows}
        assert row_map["acc1"].used_percent_avg == pytest.approx(20.0)
        assert row_map["acc2"].used_percent_avg == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_usage_repository_latest_by_account_returns_latest_per_account(db_setup):
    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(_make_account("acc1", "acc1@example.com"))
        await accounts_repo.upsert(_make_account("acc2", "acc2@example.com"))
    async with SessionLocal() as session:
        repo = UsageRepository(session)
        now = utcnow()

        await repo.add_entry("acc1", 10.0, recorded_at=now - timedelta(minutes=2), window=None)
        await repo.add_entry("acc1", 20.0, recorded_at=now - timedelta(minutes=1), window="primary")
        await repo.add_entry("acc1", 99.0, recorded_at=now - timedelta(minutes=3), window="secondary")

        await repo.add_entry("acc2", 30.0, recorded_at=now - timedelta(minutes=5), window="primary")
        await repo.add_entry("acc2", 40.0, recorded_at=now - timedelta(minutes=1), window="secondary")

        latest_default = await repo.latest_by_account()
        latest_primary = await repo.latest_by_account(window="primary")
        latest_secondary = await repo.latest_by_account(window="secondary")

        assert set(latest_default) == {"acc1", "acc2"}
        assert set(latest_primary) == {"acc1", "acc2"}
        assert set(latest_secondary) == {"acc1", "acc2"}

        assert latest_default["acc1"].used_percent == pytest.approx(20.0)
        assert latest_primary["acc1"].used_percent == pytest.approx(20.0)
        assert latest_secondary["acc1"].used_percent == pytest.approx(99.0)

        assert latest_default["acc2"].used_percent == pytest.approx(30.0)
        assert latest_primary["acc2"].used_percent == pytest.approx(30.0)
        assert latest_secondary["acc2"].used_percent == pytest.approx(40.0)


@pytest.mark.asyncio
async def test_usage_repository_effective_window_reclassifies_primary_day_windows(db_setup):
    async with SessionLocal() as session:
        repo = UsageRepository(session)
        now = utcnow()

        await repo.add_entry(
            "acc1",
            10.0,
            recorded_at=now - timedelta(minutes=1),
            window="primary",
            window_minutes=10080,
        )

        latest_primary = await repo.latest_by_account(window="primary")
        latest_secondary = await repo.latest_by_account(window="secondary")
        assert "acc1" not in latest_primary
        assert "acc1" in latest_secondary
        assert latest_secondary["acc1"].window_minutes == 10080


@pytest.mark.asyncio
async def test_usage_repository_latest_primary_secondary_by_account(db_setup):
    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(_make_account("acc1", "acc1@example.com"))
        await accounts_repo.upsert(_make_account("acc2", "acc2@example.com"))
    async with SessionLocal() as session:
        repo = UsageRepository(session)
        now = utcnow()

        await repo.add_entry("acc1", 10.0, recorded_at=now - timedelta(minutes=2), window=None)
        await repo.add_entry("acc1", 20.0, recorded_at=now - timedelta(minutes=1), window="primary")
        await repo.add_entry("acc1", 99.0, recorded_at=now - timedelta(minutes=3), window="secondary")

        await repo.add_entry("acc2", 30.0, recorded_at=now - timedelta(minutes=5), window="primary")
        await repo.add_entry("acc2", 40.0, recorded_at=now - timedelta(minutes=1), window="secondary")

        primary, secondary = await repo.latest_primary_secondary_by_account()
        assert set(primary) == {"acc1", "acc2"}
        assert set(secondary) == {"acc1", "acc2"}
        assert primary["acc1"].used_percent == pytest.approx(20.0)
        assert secondary["acc1"].used_percent == pytest.approx(99.0)
        assert primary["acc2"].used_percent == pytest.approx(30.0)
        assert secondary["acc2"].used_percent == pytest.approx(40.0)


@pytest.mark.asyncio
async def test_request_logs_repository_filters(db_setup):
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)
        now = utcnow()
        await repo.add_log(
            account_id="acc1",
            request_id="req_repo_1",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=20,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=10),
        )
        await repo.add_log(
            account_id="acc2",
            request_id="req_repo_2",
            model="gpt-5.1",
            input_tokens=5,
            output_tokens=5,
            latency_ms=50,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=now - timedelta(minutes=5),
        )

        results = await repo.list_recent(limit=0, account_ids=["acc1"])
        assert len(results) == 1
        assert results[0].account_id == "acc1"

        results = await repo.list_recent(limit=0, include_success=False)
        assert len(results) == 1
        assert results[0].error_code == "rate_limit_exceeded"
