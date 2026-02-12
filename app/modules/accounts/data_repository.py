from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RequestLog, StickySession, UsageHistory


class AccountsDataRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def delete_account_data(self, account_id: str) -> None:
        await self._session.execute(delete(UsageHistory).where(UsageHistory.account_id == account_id))
        await self._session.execute(delete(RequestLog).where(RequestLog.account_id == account_id))
        await self._session.execute(delete(StickySession).where(StickySession.account_id == account_id))
        await self._session.commit()

