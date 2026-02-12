from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timezone

import pytest

from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import AccountsSessionLocal, SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.settings.repository import SettingsRepository
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _repo_factory() -> AsyncIterator[ProxyRepositories]:
    async with SessionLocal() as main_session, AccountsSessionLocal() as accounts_session:
        yield ProxyRepositories(
            accounts=AccountsRepository(accounts_session),
            usage=UsageRepository(main_session),
            request_logs=RequestLogsRepository(main_session),
            sticky_sessions=StickySessionsRepository(main_session),
            settings=SettingsRepository(main_session),
        )


@pytest.mark.asyncio
async def test_proxy_selection_strategy_usage_honors_dashboard_reset_bucket(monkeypatch, db_setup):
    monkeypatch.setenv("CODEX_LB_PROXY_SELECTION_STRATEGY", "usage")
    get_settings.cache_clear()

    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())

    account_a = Account(
        id="acc_usage_low_reset_late",
        email="usage_low_reset_late@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-a"),
        refresh_token_encrypted=encryptor.encrypt("refresh-a"),
        id_token_encrypted=encryptor.encrypt("id-a"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account_b = Account(
        id="acc_usage_high_reset_soon",
        email="usage_high_reset_soon@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-b"),
        refresh_token_encrypted=encryptor.encrypt("refresh-b"),
        id_token_encrypted=encryptor.encrypt("id-b"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )

    primary_reset = now_epoch + 3600
    secondary_a_reset = now_epoch + 5 * 24 * 3600
    secondary_b_reset = now_epoch + 3600

    async with SessionLocal() as main_session, AccountsSessionLocal() as accounts_session:
        await SettingsRepository(main_session).update(prefer_earlier_reset_accounts=True)

        accounts_repo = AccountsRepository(accounts_session)
        usage_repo = UsageRepository(main_session)
        await accounts_repo.upsert(account_a)
        await accounts_repo.upsert(account_b)

        await usage_repo.add_entry(
            account_id=account_a.id,
            used_percent=0.0,
            window="primary",
            reset_at=primary_reset,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=account_a.id,
            used_percent=0.0,
            window="secondary",
            reset_at=secondary_a_reset,
            window_minutes=10080,
        )
        await usage_repo.add_entry(
            account_id=account_b.id,
            used_percent=50.0,
            window="primary",
            reset_at=primary_reset,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=account_b.id,
            used_percent=50.0,
            window="secondary",
            reset_at=secondary_b_reset,
            window_minutes=10080,
        )

        balancer = LoadBalancer(_repo_factory)
        selection = await balancer.select_account(sticky_key="strategy_usage_reset_bucket")

        assert selection.account is not None
        assert selection.account.id == account_b.id


@pytest.mark.asyncio
async def test_proxy_selection_strategy_waste_pressure_overrides_dashboard(monkeypatch, db_setup):
    monkeypatch.setenv("CODEX_LB_PROXY_SELECTION_STRATEGY", "waste_pressure")
    get_settings.cache_clear()

    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())

    account_pro = Account(
        id="acc_pro_reset_late",
        email="pro_reset_late@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("access-pro"),
        refresh_token_encrypted=encryptor.encrypt("refresh-pro"),
        id_token_encrypted=encryptor.encrypt("id-pro"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account_free = Account(
        id="acc_free_reset_soon",
        email="free_reset_soon@example.com",
        plan_type="free",
        access_token_encrypted=encryptor.encrypt("access-free"),
        refresh_token_encrypted=encryptor.encrypt("refresh-free"),
        id_token_encrypted=encryptor.encrypt("id-free"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )

    primary_reset = now_epoch + 3600
    secondary_pro_reset = now_epoch + 5 * 24 * 3600
    secondary_free_reset = now_epoch + 3600

    async with SessionLocal() as main_session, AccountsSessionLocal() as accounts_session:
        await SettingsRepository(main_session).update(prefer_earlier_reset_accounts=True)

        accounts_repo = AccountsRepository(accounts_session)
        usage_repo = UsageRepository(main_session)
        await accounts_repo.upsert(account_pro)
        await accounts_repo.upsert(account_free)

        for account_id, secondary_reset in (
            (account_pro.id, secondary_pro_reset),
            (account_free.id, secondary_free_reset),
        ):
            await usage_repo.add_entry(
                account_id=account_id,
                used_percent=0.0,
                window="primary",
                reset_at=primary_reset,
                window_minutes=300,
            )
            await usage_repo.add_entry(
                account_id=account_id,
                used_percent=0.0,
                window="secondary",
                reset_at=secondary_reset,
                window_minutes=10080,
            )

        balancer = LoadBalancer(_repo_factory)
        selection = await balancer.select_account(sticky_key="strategy_waste_pressure_override")

        assert selection.account is not None
        assert selection.account.id == account_pro.id


@pytest.mark.asyncio
async def test_proxy_selection_strategy_db_stickiness_persists_and_reallocates(monkeypatch, db_setup):
    monkeypatch.setenv("CODEX_LB_PROXY_SELECTION_STRATEGY", "waste_pressure")
    monkeypatch.setenv("CODEX_LB_STICKY_SESSIONS_BACKEND", "db")
    get_settings.cache_clear()

    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    primary_reset = now_epoch + 3600

    account_soon = Account(
        id="acc_plus_reset_soon",
        email="plus_reset_soon@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-soon"),
        refresh_token_encrypted=encryptor.encrypt("refresh-soon"),
        id_token_encrypted=encryptor.encrypt("id-soon"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account_late = Account(
        id="acc_pro_reset_late_2",
        email="pro_reset_late_2@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("access-late"),
        refresh_token_encrypted=encryptor.encrypt("refresh-late"),
        id_token_encrypted=encryptor.encrypt("id-late"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )

    sticky_key = "sticky_db_realloc"

    async with SessionLocal() as main_session, AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        usage_repo = UsageRepository(main_session)
        await accounts_repo.upsert(account_soon)
        await accounts_repo.upsert(account_late)

        # Initial: plus resets soon (1h), pro resets late (7d) => plus has higher waste pressure.
        await usage_repo.add_entry(
            account_id=account_soon.id,
            used_percent=0.0,
            window="primary",
            reset_at=primary_reset,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=account_soon.id,
            used_percent=0.0,
            window="secondary",
            reset_at=now_epoch + 3600,
            window_minutes=10080,
        )
        await usage_repo.add_entry(
            account_id=account_late.id,
            used_percent=0.0,
            window="primary",
            reset_at=primary_reset,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=account_late.id,
            used_percent=0.0,
            window="secondary",
            reset_at=now_epoch + 7 * 24 * 3600,
            window_minutes=10080,
        )

        balancer_1 = LoadBalancer(_repo_factory)
        first = await balancer_1.select_account(sticky_key=sticky_key)
        assert first.account is not None
        assert first.account.id == account_soon.id

        # Update usage to flip pressure: plus resets late, pro resets soon.
        await usage_repo.add_entry(
            account_id=account_soon.id,
            used_percent=0.0,
            window="secondary",
            reset_at=now_epoch + 7 * 24 * 3600,
            window_minutes=10080,
        )
        await usage_repo.add_entry(
            account_id=account_late.id,
            used_percent=0.0,
            window="secondary",
            reset_at=now_epoch + 3600,
            window_minutes=10080,
        )

        balancer_2 = LoadBalancer(_repo_factory)
        reallocated = await balancer_2.select_account(sticky_key=sticky_key, reallocate_sticky=True)
        assert reallocated.account is not None
        assert reallocated.account.id == account_late.id

        # New balancer instance should still pick the pinned account from the DB sticky backend.
        balancer_3 = LoadBalancer(_repo_factory)
        pinned = await balancer_3.select_account(sticky_key=sticky_key)
        assert pinned.account is not None
        assert pinned.account.id == account_late.id
