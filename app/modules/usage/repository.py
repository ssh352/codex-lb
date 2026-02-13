from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.usage.types import UsageAggregateRow
from app.core.utils.time import utcnow
from app.db.models import UsageHistory
from app.modules.proxy.rate_limit_cache import invalidate_rate_limit_headers_cache

_SECONDARY_WINDOW_THRESHOLD_MINUTES = 24 * 60


def _effective_window_key_expr():
    raw = func.coalesce(UsageHistory.window, "primary")
    return case(
        (
            and_(
                raw == "primary",
                UsageHistory.window_minutes >= _SECONDARY_WINDOW_THRESHOLD_MINUTES,
            ),
            "secondary",
        ),
        else_=raw,
    )


class UsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()
        invalidate_rate_limit_headers_cache()

    async def rollback(self) -> None:
        await self._session.rollback()

    async def add_entry(
        self,
        account_id: str,
        used_percent: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        recorded_at: datetime | None = None,
        window: str | None = None,
        reset_at: int | None = None,
        window_minutes: int | None = None,
        credits_has: bool | None = None,
        credits_unlimited: bool | None = None,
        credits_balance: float | None = None,
        *,
        commit: bool = True,
    ) -> UsageHistory:
        entry = UsageHistory(
            account_id=account_id,
            used_percent=used_percent,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            window=window,
            reset_at=reset_at,
            window_minutes=window_minutes,
            credits_has=credits_has,
            credits_unlimited=credits_unlimited,
            credits_balance=credits_balance,
            recorded_at=recorded_at or utcnow(),
        )
        self._session.add(entry)
        if commit:
            await self._session.commit()
            await self._session.refresh(entry)
            invalidate_rate_limit_headers_cache()
        return entry

    async def aggregate_since(
        self,
        since: datetime,
        window: str | None = None,
    ) -> list[UsageAggregateRow]:
        conditions = [UsageHistory.recorded_at >= since]
        if window:
            effective_window = _effective_window_key_expr()
            conditions.append(effective_window == window)
        stmt = (
            select(
                UsageHistory.account_id,
                func.avg(UsageHistory.used_percent).label("used_percent_avg"),
                func.sum(UsageHistory.input_tokens).label("input_tokens_sum"),
                func.sum(UsageHistory.output_tokens).label("output_tokens_sum"),
                func.count(UsageHistory.id).label("samples"),
                func.max(UsageHistory.recorded_at).label("last_recorded_at"),
                func.max(UsageHistory.reset_at).label("reset_at_max"),
                func.max(UsageHistory.window_minutes).label("window_minutes_max"),
            )
            .where(*conditions)
            .group_by(UsageHistory.account_id)
        )
        result = await self._session.execute(stmt)
        rows = result.all()
        return [
            UsageAggregateRow(
                account_id=row.account_id,
                used_percent_avg=float(row.used_percent_avg) if row.used_percent_avg is not None else None,
                input_tokens_sum=int(row.input_tokens_sum) if row.input_tokens_sum is not None else None,
                output_tokens_sum=int(row.output_tokens_sum) if row.output_tokens_sum is not None else None,
                samples=int(row.samples),
                last_recorded_at=row.last_recorded_at,
                reset_at_max=int(row.reset_at_max) if row.reset_at_max is not None else None,
                window_minutes_max=int(row.window_minutes_max) if row.window_minutes_max is not None else None,
            )
            for row in rows
        ]

    async def latest_by_account(self, window: str | None = None) -> dict[str, UsageHistory]:
        effective_window = _effective_window_key_expr()
        if window is None or window == "primary":
            conditions = effective_window == "primary"
        else:
            conditions = effective_window == window

        ranked = (
            select(
                UsageHistory.id.label("id"),
                func.row_number()
                .over(
                    partition_by=UsageHistory.account_id,
                    order_by=(UsageHistory.recorded_at.desc(), UsageHistory.id.desc()),
                )
                .label("rn"),
            )
            .where(conditions)
            .subquery()
        )

        stmt = (
            select(UsageHistory)
            .join(ranked, UsageHistory.id == ranked.c.id)
            .where(ranked.c.rn == 1)
            .order_by(UsageHistory.account_id)
        )
        result = await self._session.execute(stmt)
        entries = list(result.scalars().all())
        return {entry.account_id: entry for entry in entries}

    async def latest_primary_secondary_by_account(
        self,
    ) -> tuple[dict[str, UsageHistory], dict[str, UsageHistory]]:
        # Treat window=NULL as primary for historical compatibility.
        window_key = _effective_window_key_expr()

        ranked = (
            select(
                UsageHistory.id.label("id"),
                window_key.label("window_key"),
                func.row_number()
                .over(
                    partition_by=(UsageHistory.account_id, window_key),
                    order_by=(UsageHistory.recorded_at.desc(), UsageHistory.id.desc()),
                )
                .label("rn"),
            )
            .where(or_(UsageHistory.window.in_(("primary", "secondary")), UsageHistory.window.is_(None)))
            .subquery()
        )

        stmt = (
            select(UsageHistory, ranked.c.window_key)
            .join(ranked, UsageHistory.id == ranked.c.id)
            .where(ranked.c.rn == 1)
            .order_by(UsageHistory.account_id)
        )
        result = await self._session.execute(stmt)

        primary: dict[str, UsageHistory] = {}
        secondary: dict[str, UsageHistory] = {}
        for entry, window_value in result.all():
            if window_value == "primary":
                primary[entry.account_id] = entry
            elif window_value == "secondary":
                secondary[entry.account_id] = entry

        return primary, secondary

    async def latest_window_minutes(self, window: str) -> int | None:
        effective_window = _effective_window_key_expr()
        conditions = effective_window == window
        result = await self._session.execute(select(func.max(UsageHistory.window_minutes)).where(conditions))
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None
