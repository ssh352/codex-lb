from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, RequestLog, UsageHistory
from app.modules.accounts.repository import AccountsRepository, AccountStatusUpdate
from app.modules.request_logs.aggregates import RequestLogsUsageAggregates
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.settings.repository import SettingsRepository
from app.modules.usage.repository import UsageRepository


class DashboardRepository:
    def __init__(self, *, main_session: AsyncSession, accounts_session: AsyncSession) -> None:
        self._accounts_repo = AccountsRepository(accounts_session)
        self._usage_repo = UsageRepository(main_session)
        self._logs_repo = RequestLogsRepository(main_session)
        self._settings_repo = SettingsRepository(main_session)

    async def list_accounts(self) -> list[Account]:
        return await self._accounts_repo.list_accounts()

    async def bulk_update_status_fields(self, updates: Sequence[AccountStatusUpdate]) -> int:
        return await self._accounts_repo.bulk_update_status_fields(updates)

    async def bulk_set_accounts_active(self, account_ids: Sequence[str]) -> int:
        return await self._accounts_repo.bulk_set_active(account_ids)

    async def bulk_clear_accounts_reset_at(self, account_ids: Sequence[str]) -> int:
        return await self._accounts_repo.bulk_clear_reset_at(account_ids)

    async def latest_usage_by_account(self, window: str) -> dict[str, UsageHistory]:
        return await self._usage_repo.latest_by_account(window=window)

    async def latest_primary_secondary_usage_by_account(
        self,
    ) -> tuple[dict[str, UsageHistory], dict[str, UsageHistory]]:
        return await self._usage_repo.latest_primary_secondary_by_account()

    async def latest_window_minutes(self, window: str) -> int | None:
        return await self._usage_repo.latest_window_minutes(window)

    async def list_logs_since(self, since: datetime) -> list[RequestLog]:
        return await self._logs_repo.list_since(since)

    async def aggregate_request_logs_usage_since(self, since: datetime) -> RequestLogsUsageAggregates:
        return await self._logs_repo.aggregate_usage_since(since)

    async def list_recent_logs(self, limit: int, offset: int) -> list[RequestLog]:
        return await self._logs_repo.list_recent(limit=limit, offset=offset)

    async def pinned_account_ids(self) -> list[str]:
        return await self._settings_repo.pinned_account_ids()
