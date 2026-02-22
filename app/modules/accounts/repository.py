from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, AccountStatus


class AccountsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_account(self, account_id: str) -> Account | None:
        return await self._session.get(Account, account_id)

    async def list_accounts(self) -> list[Account]:
        result = await self._session.execute(select(Account).order_by(Account.email))
        return list(result.scalars().all())

    async def find_account_ids_by_email_search(self, search: str, *, limit: int = 5000) -> list[str]:
        raw = (search or "").strip()
        if not raw:
            return []
        pattern = f"%{raw}%"
        result = await self._session.execute(
            select(Account.id).where(Account.email.ilike(pattern)).order_by(Account.email).limit(limit)
        )
        return [row[0] for row in result.all() if row and row[0]]

    async def upsert(self, account: Account) -> Account:
        existing = await self._session.get(Account, account.id)
        if existing:
            _apply_account_updates(existing, account)
            await self._session.commit()
            await self._session.refresh(existing)
            return existing

        result = await self._session.execute(select(Account).where(Account.email == account.email))
        existing_by_email = result.scalar_one_or_none()
        if existing_by_email:
            _apply_account_updates(existing_by_email, account)
            await self._session.commit()
            await self._session.refresh(existing_by_email)
            return existing_by_email

        self._session.add(account)
        await self._session.commit()
        await self._session.refresh(account)
        return account

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
    ) -> bool:
        # `reset_at` is a persisted "blocked until" hint and is only meaningful when the account is
        # in a blocked state (RATE_LIMITED / QUOTA_EXCEEDED). Clear it for all other statuses to
        # avoid stale/inconsistent DB state (e.g., ACTIVE with a non-null reset timestamp).
        normalized_reset_at = _normalize_reset_at_for_status(status, reset_at)
        result = await self._session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(status=status, deactivation_reason=deactivation_reason, reset_at=normalized_reset_at)
            .returning(Account.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def bulk_update_status_fields(self, updates: Sequence[AccountStatusUpdate]) -> int:
        if not updates:
            return 0
        updated = 0
        for entry in updates:
            normalized_reset_at = _normalize_reset_at_for_status(entry.status, entry.reset_at)
            result = await self._session.execute(
                update(Account)
                .where(Account.id == entry.account_id)
                .values(
                    status=entry.status,
                    deactivation_reason=entry.deactivation_reason,
                    reset_at=normalized_reset_at,
                )
                .returning(Account.id)
            )
            if result.scalar_one_or_none() is not None:
                updated += 1
        await self._session.commit()
        return updated

    async def bulk_set_active(self, account_ids: Sequence[str]) -> int:
        ids = [value for value in account_ids if value]
        if not ids:
            return 0
        result = await self._session.execute(
            update(Account)
            .where(Account.id.in_(ids))
            .values(
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
                reset_at=None,
            )
            .returning(Account.id)
        )
        await self._session.commit()
        return len(result.scalars().all())

    async def bulk_clear_reset_at(self, account_ids: Sequence[str]) -> int:
        ids = [value for value in account_ids if value]
        if not ids:
            return 0
        result = await self._session.execute(
            update(Account).where(Account.id.in_(ids)).values(reset_at=None).returning(Account.id)
        )
        await self._session.commit()
        return len(result.scalars().all())

    async def delete(self, account_id: str) -> bool:
        result = await self._session.execute(delete(Account).where(Account.id == account_id).returning(Account.id))
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def update_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
    ) -> bool:
        values = {
            "access_token_encrypted": access_token_encrypted,
            "refresh_token_encrypted": refresh_token_encrypted,
            "id_token_encrypted": id_token_encrypted,
            "last_refresh": last_refresh,
        }
        if plan_type is not None:
            values["plan_type"] = plan_type
        if email is not None:
            values["email"] = email
        if chatgpt_account_id is not None:
            values["chatgpt_account_id"] = chatgpt_account_id
        result = await self._session.execute(
            update(Account).where(Account.id == account_id).values(**values).returning(Account.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None


def _apply_account_updates(target: Account, source: Account) -> None:
    target.chatgpt_account_id = source.chatgpt_account_id
    target.email = source.email
    target.plan_type = source.plan_type
    target.access_token_encrypted = source.access_token_encrypted
    target.refresh_token_encrypted = source.refresh_token_encrypted
    target.id_token_encrypted = source.id_token_encrypted
    target.last_refresh = source.last_refresh
    target.status = source.status
    target.deactivation_reason = source.deactivation_reason
    target.reset_at = _normalize_reset_at_for_status(source.status, source.reset_at)


@dataclass(frozen=True, slots=True)
class AccountStatusUpdate:
    account_id: str
    status: AccountStatus
    deactivation_reason: str | None
    reset_at: int | None


def _normalize_reset_at_for_status(status: AccountStatus, reset_at: int | None) -> int | None:
    if status in (AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED):
        return reset_at
    return None
