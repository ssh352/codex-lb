from __future__ import annotations

import json
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DashboardSettings

_SETTINGS_ID = 1


def _normalize_account_ids(value: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in value:
        account_id = raw.strip()
        if not account_id or account_id in seen:
            continue
        seen.add(account_id)
        normalized.append(account_id)
    return normalized


def _encode_pinned_account_ids(value: Sequence[str]) -> str:
    normalized = _normalize_account_ids(value)
    return json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))


def _decode_pinned_account_ids(value: str | None) -> list[str]:
    if value is None:
        return []
    raw = value.strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("dashboard_settings.pinned_account_ids_json must be a JSON array of strings") from exc
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise ValueError("dashboard_settings.pinned_account_ids_json must be a JSON array of strings")
    return _normalize_account_ids(parsed)


class SettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(self) -> DashboardSettings:
        existing = await self._session.get(DashboardSettings, _SETTINGS_ID)
        if existing is not None:
            return existing

        row = DashboardSettings(
            id=_SETTINGS_ID,
            sticky_threads_enabled=True,
            prefer_earlier_reset_accounts=False,
            pinned_account_ids_json="[]",
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def update(
        self,
        *,
        prefer_earlier_reset_accounts: bool | None = None,
        pinned_account_ids: Sequence[str] | None = None,
    ) -> DashboardSettings:
        settings = await self.get_or_create()
        if prefer_earlier_reset_accounts is not None:
            settings.prefer_earlier_reset_accounts = prefer_earlier_reset_accounts
        if pinned_account_ids is not None:
            settings.pinned_account_ids_json = _encode_pinned_account_ids(pinned_account_ids)
        await self.commit_refresh(settings)
        return settings

    async def pinned_account_ids(self) -> list[str]:
        settings = await self.get_or_create()
        return _decode_pinned_account_ids(settings.pinned_account_ids_json)

    async def remove_pinned_account_ids(self, account_ids: Sequence[str]) -> DashboardSettings:
        normalized_remove = set(_normalize_account_ids(account_ids))
        if not normalized_remove:
            return await self.get_or_create()

        settings = await self.get_or_create()
        existing = _decode_pinned_account_ids(settings.pinned_account_ids_json)
        updated = [account_id for account_id in existing if account_id not in normalized_remove]
        if updated == existing:
            return settings

        settings.pinned_account_ids_json = _encode_pinned_account_ids(updated)
        await self.commit_refresh(settings)
        return settings

    async def commit_refresh(self, settings: DashboardSettings) -> None:
        await self._session.commit()
        await self._session.refresh(settings)
