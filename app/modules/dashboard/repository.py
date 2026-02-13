from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, RequestLog, UsageHistory
from app.modules.accounts.repository import AccountsRepository
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

    async def latest_usage_by_account(self, window: str) -> dict[str, UsageHistory]:
        return await self._usage_repo.latest_by_account(window=window)

    async def latest_window_minutes(self, window: str) -> int | None:
        return await self._usage_repo.latest_window_minutes(window)

    async def list_logs_since(self, since: datetime) -> list[RequestLog]:
        return await self._logs_repo.list_since(since)

    async def list_recent_logs(self, limit: int, offset: int) -> list[RequestLog]:
        return await self._logs_repo.list_recent(limit=limit, offset=offset)

    async def pinned_account_ids(self) -> list[str]:
        return await self._settings_repo.pinned_account_ids()
