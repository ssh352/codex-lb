from __future__ import annotations

from datetime import datetime

import anyio
from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy import exc as sa_exc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.request_id import ensure_request_id
from app.core.utils.time import utcnow
from app.db.models import RequestLog
from app.modules.request_logs.aggregates import (
    RequestLogModelUsageAggregate,
    RequestLogsUsageAggregates,
)


class RequestLogsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_since(self, since: datetime) -> list[RequestLog]:
        result = await self._session.execute(select(RequestLog).where(RequestLog.requested_at >= since))
        return list(result.scalars().all())

    async def add_log(
        self,
        account_id: str,
        request_id: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int | None,
        status: str,
        error_code: str | None,
        error_message: str | None = None,
        requested_at: datetime | None = None,
        cached_input_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        reasoning_effort: str | None = None,
        prompt_cache_key_hash: str | None = None,
        codex_session_id: str | None = None,
        codex_conversation_id: str | None = None,
    ) -> RequestLog:
        resolved_request_id = ensure_request_id(request_id)
        log = RequestLog(
            account_id=account_id,
            request_id=resolved_request_id,
            codex_session_id=codex_session_id,
            codex_conversation_id=codex_conversation_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            reasoning_tokens=reasoning_tokens,
            reasoning_effort=reasoning_effort,
            latency_ms=latency_ms,
            status=status,
            error_code=error_code,
            error_message=error_message,
            prompt_cache_key_hash=prompt_cache_key_hash,
            requested_at=requested_at or utcnow(),
        )
        self._session.add(log)
        try:
            await self._session.commit()
            await self._session.refresh(log)
            return log
        except sa_exc.ResourceClosedError:
            return log
        except BaseException:
            await _safe_rollback(self._session)
            raise

    async def list_recent(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        search_account_ids: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        account_ids: list[str] | None = None,
        model_options: list[tuple[str, str | None]] | None = None,
        models: list[str] | None = None,
        reasoning_efforts: list[str] | None = None,
        include_success: bool = True,
        include_error_other: bool = True,
        error_codes_in: list[str] | None = None,
        error_codes_excluding: list[str] | None = None,
    ) -> list[RequestLog]:
        conditions = []
        if since is not None:
            conditions.append(RequestLog.requested_at >= since)
        if until is not None:
            conditions.append(RequestLog.requested_at <= until)
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))

        if model_options:
            pair_conditions = []
            for model, effort in model_options:
                base = (model or "").strip()
                if not base:
                    continue
                if effort is None:
                    pair_conditions.append(and_(RequestLog.model == base, RequestLog.reasoning_effort.is_(None)))
                else:
                    pair_conditions.append(and_(RequestLog.model == base, RequestLog.reasoning_effort == effort))
            if pair_conditions:
                conditions.append(or_(*pair_conditions))
        else:
            if models:
                conditions.append(RequestLog.model.in_(models))
            if reasoning_efforts:
                conditions.append(RequestLog.reasoning_effort.in_(reasoning_efforts))

        status_conditions = []
        if include_success:
            status_conditions.append(RequestLog.status == "success")
        if error_codes_in:
            status_conditions.append(and_(RequestLog.status == "error", RequestLog.error_code.in_(error_codes_in)))
        if include_error_other:
            error_clause = [RequestLog.status == "error"]
            if error_codes_excluding:
                error_clause.append(
                    or_(
                        RequestLog.error_code.is_(None),
                        ~RequestLog.error_code.in_(error_codes_excluding),
                    )
                )
            status_conditions.append(and_(*error_clause))
        if status_conditions:
            conditions.append(or_(*status_conditions))
        if search:
            search_pattern = f"%{search}%"
            search_conditions = [
                RequestLog.account_id.ilike(search_pattern),
                RequestLog.request_id.ilike(search_pattern),
                RequestLog.codex_session_id.ilike(search_pattern),
                RequestLog.codex_conversation_id.ilike(search_pattern),
                RequestLog.model.ilike(search_pattern),
                RequestLog.reasoning_effort.ilike(search_pattern),
                RequestLog.status.ilike(search_pattern),
                RequestLog.error_code.ilike(search_pattern),
                RequestLog.error_message.ilike(search_pattern),
                cast(RequestLog.requested_at, String).ilike(search_pattern),
                cast(RequestLog.input_tokens, String).ilike(search_pattern),
                cast(RequestLog.output_tokens, String).ilike(search_pattern),
                cast(RequestLog.cached_input_tokens, String).ilike(search_pattern),
                cast(RequestLog.reasoning_tokens, String).ilike(search_pattern),
                cast(RequestLog.latency_ms, String).ilike(search_pattern),
            ]
            email_account_ids = [value for value in (search_account_ids or []) if value]
            if email_account_ids:
                search_conditions.append(RequestLog.account_id.in_(email_account_ids))
            conditions.append(or_(*search_conditions))

        stmt = select(RequestLog).order_by(RequestLog.requested_at.desc())
        if conditions:
            stmt = stmt.where(and_(*conditions))
        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_filter_options(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        include_success: bool = True,
        include_error_other: bool = True,
        error_codes_in: list[str] | None = None,
        error_codes_excluding: list[str] | None = None,
    ) -> tuple[list[str], list[tuple[str, str | None]]]:
        conditions = []
        if since is not None:
            conditions.append(RequestLog.requested_at >= since)
        if until is not None:
            conditions.append(RequestLog.requested_at <= until)
        status_conditions = []
        if include_success:
            status_conditions.append(RequestLog.status == "success")
        if error_codes_in:
            status_conditions.append(and_(RequestLog.status == "error", RequestLog.error_code.in_(error_codes_in)))
        if include_error_other:
            error_clause = [RequestLog.status == "error"]
            if error_codes_excluding:
                error_clause.append(
                    or_(
                        RequestLog.error_code.is_(None),
                        ~RequestLog.error_code.in_(error_codes_excluding),
                    )
                )
            status_conditions.append(and_(*error_clause))
        if status_conditions:
            conditions.append(or_(*status_conditions))

        account_stmt = select(RequestLog.account_id).distinct().order_by(RequestLog.account_id.asc())
        model_stmt = (
            select(RequestLog.model, RequestLog.reasoning_effort)
            .distinct()
            .order_by(RequestLog.model.asc(), RequestLog.reasoning_effort.asc())
        )
        if conditions:
            clause = and_(*conditions)
            account_stmt = account_stmt.where(clause)
            model_stmt = model_stmt.where(clause)

        account_rows = await self._session.execute(account_stmt)
        model_rows = await self._session.execute(model_stmt)

        account_ids = [row[0] for row in account_rows.all() if row[0]]
        model_options = [(row[0], row[1]) for row in model_rows.all() if row[0]]
        return account_ids, model_options

    async def aggregate_usage_since(
        self,
        since: datetime,
        *,
        until: datetime | None = None,
    ) -> RequestLogsUsageAggregates:
        conditions = [RequestLog.requested_at >= since]
        if until is not None:
            conditions.append(RequestLog.requested_at <= until)

        effective_output = func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens)
        tokens_expr = func.coalesce(RequestLog.input_tokens, 0) + func.coalesce(effective_output, 0)

        cached_raw = RequestLog.cached_input_tokens
        cached_nonneg = case((cached_raw < 0, 0), else_=cached_raw)
        cached_nonneg_coalesced = func.coalesce(cached_nonneg, 0)
        cached_clamped = case(
            (
                and_(
                    RequestLog.input_tokens.is_not(None),
                    cached_nonneg_coalesced > RequestLog.input_tokens,
                ),
                RequestLog.input_tokens,
            ),
            else_=cached_nonneg_coalesced,
        )

        summary_stmt = select(
            func.count(RequestLog.id).label("total_requests"),
            func.coalesce(func.sum(case((RequestLog.status != "success", 1), else_=0)), 0).label("error_requests"),
            func.coalesce(func.sum(tokens_expr), 0).label("tokens_sum"),
            func.coalesce(func.sum(cached_clamped), 0).label("cached_input_tokens_sum"),
        ).where(and_(*conditions))
        summary_result = await self._session.execute(summary_stmt)
        summary = summary_result.one()

        top_error_stmt = (
            select(RequestLog.error_code, func.count(RequestLog.id).label("count"))
            .where(
                and_(
                    *conditions,
                    RequestLog.status != "success",
                    RequestLog.error_code.is_not(None),
                    RequestLog.error_code != "",
                )
            )
            .group_by(RequestLog.error_code)
            .order_by(func.count(RequestLog.id).desc(), RequestLog.error_code.asc())
            .limit(1)
        )
        top_error_result = await self._session.execute(top_error_stmt)
        top_error_row = top_error_result.one_or_none()
        top_error = str(top_error_row[0]) if top_error_row and top_error_row[0] else None

        by_model_stmt = (
            select(
                RequestLog.model.label("model"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens_sum"),
                func.coalesce(func.sum(effective_output), 0).label("output_tokens_sum"),
                func.coalesce(func.sum(cached_clamped), 0).label("cached_input_tokens_sum"),
            )
            .where(
                and_(
                    *conditions,
                    RequestLog.model.is_not(None),
                    RequestLog.model != "",
                    RequestLog.input_tokens.is_not(None),
                    effective_output.is_not(None),
                )
            )
            .group_by(RequestLog.model)
            .order_by(RequestLog.model.asc())
        )
        by_model_result = await self._session.execute(by_model_stmt)
        by_model_rows = by_model_result.all()
        by_model = [
            RequestLogModelUsageAggregate(
                model=str(row.model),
                input_tokens_sum=int(row.input_tokens_sum or 0),
                output_tokens_sum=int(row.output_tokens_sum or 0),
                cached_input_tokens_sum=int(row.cached_input_tokens_sum or 0),
            )
            for row in by_model_rows
            if row and row.model
        ]

        return RequestLogsUsageAggregates(
            total_requests=int(summary.total_requests or 0),
            error_requests=int(summary.error_requests or 0),
            tokens_sum=int(summary.tokens_sum or 0),
            cached_input_tokens_sum=int(summary.cached_input_tokens_sum or 0),
            top_error=top_error,
            by_model=by_model,
        )


async def _safe_rollback(session: AsyncSession) -> None:
    if not session.in_transaction():
        return
    try:
        with anyio.CancelScope(shield=True):
            await session.rollback()
    except BaseException:
        return
