from __future__ import annotations

from dataclasses import dataclass

from app.modules.settings.repository import SettingsRepository


@dataclass(frozen=True, slots=True)
class DashboardSettingsData:
    prefer_earlier_reset_accounts: bool
    pinned_account_ids: list[str]


@dataclass(frozen=True, slots=True)
class DashboardSettingsUpdateData:
    prefer_earlier_reset_accounts: bool
    pinned_account_ids: list[str] | None = None


class SettingsService:
    def __init__(self, repository: SettingsRepository) -> None:
        self._repository = repository

    async def get_settings(self) -> DashboardSettingsData:
        row = await self._repository.get_or_create()
        return DashboardSettingsData(
            prefer_earlier_reset_accounts=row.prefer_earlier_reset_accounts,
            pinned_account_ids=await self._repository.pinned_account_ids(),
        )

    async def update_settings(self, payload: DashboardSettingsUpdateData) -> DashboardSettingsData:
        row = await self._repository.update(
            prefer_earlier_reset_accounts=payload.prefer_earlier_reset_accounts,
            pinned_account_ids=payload.pinned_account_ids,
        )
        return DashboardSettingsData(
            prefer_earlier_reset_accounts=row.prefer_earlier_reset_accounts,
            pinned_account_ids=await self._repository.pinned_account_ids(),
        )
