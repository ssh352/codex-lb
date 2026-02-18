from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import get_settings
from app.core.metrics.metrics import SecondaryQuotaEstimateObservation
from app.core.usage.pricing import UsageTokens, calculate_cost_from_usage, get_pricing_for_model
from app.core.utils.time import utcnow
from app.db.models import RequestLog, UsageHistory


async def compute_secondary_quota_estimates_7d(
    session: AsyncSession,
    *,
    account_ids: list[str],
) -> list[SecondaryQuotaEstimateObservation]:
    if not account_ids:
        return []

    now = utcnow()
    since = now - timedelta(days=7)

    # Exclusion guard: ignore accounts where the provider's secondary used% meter is already materially >0 for the
    # current weekly cycle, but codex-lb has no proxy request logs that could account for that early-cycle movement.
    #
    # Concretely, we exclude an account when:
    # - The first observed `usage_history` sample in the current cycle arrives "late" after the inferred cycle start
    #   (beyond a grace window), AND
    # - That first sample is already at least `_CYCLE_START_MIN_USED_PP` percentage points used, AND
    # - There are zero `request_logs` in [cycle_start, first_usage_sample_time).
    #
    # This situation does not prove external usage as fact; it only proves "meter advanced without matching proxy
    # spend in SQLite", which can happen if usage occurred outside codex-lb OR if codex-lb wasn't logging early in the
    # cycle (down, misconfigured, etc.). In either case, `spend / (used_pp/100)` would be biased low, so we suppress
    # the estimate rather than emit a misleading number.
    _CYCLE_START_GRACE_MINUTES = 60.0
    _CYCLE_START_MIN_USED_PP = 5.0
    _UNEXPLAINED_JUMP_MIN_DELTA_PP = 5.0
    # We scale the "long gap" threshold with the configured usage refresh interval so it continues to mean
    # "we missed many expected usage snapshots" if operators change `CODEX_LB_USAGE_REFRESH_INTERVAL_SECONDS`.
    #
    # The absolute floor keeps us in "hours" territory for the default 60s refresh, avoiding false positives from
    # normal sampling jitter or brief outages.
    refresh_interval_minutes = get_settings().usage_refresh_interval_seconds / 60.0
    _UNEXPLAINED_JUMP_MIN_GAP_MINUTES = max(180.0, 10.0 * refresh_interval_minutes)

    # These quota-estimate inputs should reflect the current weekly cycle, clipped to the last 7 days.
    #
    # We infer the most recent reset start from the latest usage sample:
    # - `reset_at` is the next weekly boundary in epoch seconds.
    # - `window_minutes` is the weekly window size (typically 10080).
    # - The cycle start is `reset_at - window_minutes*60`.
    #
    # IMPORTANT: "pp" (percentage points) here is NOT derived from request logs. It comes from the
    # latest `usage_history.used_percent` snapshot for the secondary window, which is itself already
    # "percent used since cycle start". In practice, our SQLite-derived `used_delta_pp_7d` is
    # equivalent to the latest observed `used_percent` for the current cycle (clamped to [0, 100]),
    # not a sum of per-sample deltas.
    #
    # The numerator (spend) IS derived from request logs: we sum tokens from `request_logs` and
    # apply model pricing over the same time range (cycle start, clipped to last 7 days).
    since_literal = since.isoformat(sep=" ")
    latest_usage_rows = (
        select(
            UsageHistory.account_id.label("account_id"),
            UsageHistory.used_percent.label("used_percent"),
            UsageHistory.reset_at.label("reset_at"),
            UsageHistory.window_minutes.label("window_minutes"),
            func.row_number()
            .over(
                partition_by=UsageHistory.account_id,
                order_by=UsageHistory.recorded_at.desc(),
            )
            .label("rn"),
        )
        .where(
            and_(
                UsageHistory.account_id.in_(account_ids),
                UsageHistory.window == "secondary",
                UsageHistory.recorded_at <= now,
                UsageHistory.recorded_at >= since,
                UsageHistory.reset_at.is_not(None),
                UsageHistory.window_minutes.is_not(None),
            )
        )
        .subquery()
    )
    latest_usage = (
        select(
            latest_usage_rows.c.account_id,
            latest_usage_rows.c.used_percent,
            latest_usage_rows.c.reset_at,
            latest_usage_rows.c.window_minutes,
        )
        .where(latest_usage_rows.c.rn == 1)
        .subquery()
    )
    reset_start_epoch = latest_usage.c.reset_at - (latest_usage.c.window_minutes * 60)
    reset_start_dt = func.datetime(reset_start_epoch, "unixepoch")
    cutover_dt = case(
        (reset_start_dt > since_literal, reset_start_dt),
        else_=since_literal,
    )

    usage_rows = await session.execute(select(latest_usage))
    latest_by_account_id: dict[str, tuple[float, int, int]] = {}
    for row in usage_rows.all():
        account_id = str(row.account_id)
        used_percent = float(row.used_percent or 0.0)
        reset_at = int(row.reset_at)
        window_minutes = int(row.window_minutes)
        latest_by_account_id[account_id] = (used_percent, reset_at, window_minutes)

    if not latest_by_account_id:
        return []

    # Earliest usage sample in the current cycle (identified by matching the latest `reset_at` boundary).
    first_usage_rows = (
        select(
            UsageHistory.account_id.label("account_id"),
            UsageHistory.recorded_at.label("recorded_at"),
            UsageHistory.used_percent.label("used_percent"),
            func.row_number()
            .over(
                partition_by=UsageHistory.account_id,
                order_by=UsageHistory.recorded_at.asc(),
            )
            .label("rn"),
        )
        .select_from(UsageHistory)
        .join(latest_usage, latest_usage.c.account_id == UsageHistory.account_id)
        .where(
            and_(
                UsageHistory.account_id.in_(list(latest_by_account_id.keys())),
                UsageHistory.window == "secondary",
                UsageHistory.recorded_at >= since,
                UsageHistory.recorded_at <= now,
                UsageHistory.reset_at == latest_usage.c.reset_at,
            )
        )
        .subquery()
    )
    first_usage = (
        select(
            first_usage_rows.c.account_id,
            first_usage_rows.c.recorded_at.label("first_usage_at"),
            first_usage_rows.c.used_percent.label("first_used_percent"),
        )
        .where(first_usage_rows.c.rn == 1)
        .subquery()
    )

    logs_before_first_usage_stmt = (
        select(
            RequestLog.account_id.label("account_id"),
            func.count().label("logs_before_first_usage"),
        )
        .select_from(RequestLog)
        .join(latest_usage, latest_usage.c.account_id == RequestLog.account_id)
        .join(first_usage, first_usage.c.account_id == RequestLog.account_id)
        .where(
            and_(
                RequestLog.account_id.in_(list(latest_by_account_id.keys())),
                RequestLog.requested_at >= reset_start_dt,
                RequestLog.requested_at < first_usage.c.first_usage_at,
            )
        )
        .group_by(RequestLog.account_id)
    )
    logs_before_rows = await session.execute(logs_before_first_usage_stmt)
    logs_before_by_account_id: dict[str, int] = {
        str(row.account_id): int(row.logs_before_first_usage or 0) for row in logs_before_rows.all()
    }

    first_usage_rows_result = await session.execute(select(first_usage))
    first_usage_by_account_id: dict[str, tuple[datetime, float]] = {}
    for row in first_usage_rows_result.all():
        first_usage_at = row.first_usage_at
        if not isinstance(first_usage_at, datetime):
            continue
        first_usage_by_account_id[str(row.account_id)] = (first_usage_at, float(row.first_used_percent or 0.0))

    excluded_account_ids: set[str] = set()
    for account_id, (_, reset_at, window_minutes) in latest_by_account_id.items():
        first = first_usage_by_account_id.get(account_id)
        if first is None:
            # Without a sample in the current cycle, we cannot anchor cycle_start or interpret used_percent.
            excluded_account_ids.add(account_id)
            continue
        first_usage_at, first_used_percent = first
        reset_start_epoch_seconds = int(reset_at) - int(window_minutes) * 60
        # `recorded_at` is stored as a UTC-naive datetime; interpret reset_start as UTC and strip tzinfo.
        reset_start_dt_py = datetime.fromtimestamp(reset_start_epoch_seconds, tz=timezone.utc).replace(tzinfo=None)
        lag_minutes = (first_usage_at - reset_start_dt_py).total_seconds() / 60.0
        u0 = max(0.0, min(100.0, float(first_used_percent)))
        n0 = int(logs_before_by_account_id.get(account_id, 0))
        if lag_minutes > _CYCLE_START_GRACE_MINUTES and u0 >= _CYCLE_START_MIN_USED_PP and n0 == 0:
            excluded_account_ids.add(account_id)

    # Additional exclusion: suppress accounts with large secondary used% jumps across long gaps where codex-lb recorded
    # no proxy request logs in the same interval. This indicates the provider meter advanced without matching proxy
    # spend in SQLite (external usage or missing logs), which would bias the implied quota estimate low.
    #
    # We only flag long gaps (hours) to avoid false positives from small meter-update delays.
    usage_with_prev = (
        select(
            UsageHistory.account_id.label("account_id"),
            UsageHistory.recorded_at.label("recorded_at"),
            UsageHistory.used_percent.label("used_percent"),
            func.lag(UsageHistory.recorded_at)
            .over(
                partition_by=UsageHistory.account_id,
                order_by=UsageHistory.recorded_at.asc(),
            )
            .label("prev_at"),
            func.lag(UsageHistory.used_percent)
            .over(
                partition_by=UsageHistory.account_id,
                order_by=UsageHistory.recorded_at.asc(),
            )
            .label("prev_used_percent"),
        )
        .select_from(UsageHistory)
        .join(latest_usage, latest_usage.c.account_id == UsageHistory.account_id)
        .where(
            and_(
                UsageHistory.account_id.in_(list(latest_by_account_id.keys())),
                UsageHistory.window == "secondary",
                UsageHistory.recorded_at >= since,
                UsageHistory.recorded_at <= now,
                UsageHistory.reset_at == latest_usage.c.reset_at,
            )
        )
        .subquery()
    )

    delta_pp = usage_with_prev.c.used_percent - usage_with_prev.c.prev_used_percent
    gap_minutes = (func.julianday(usage_with_prev.c.recorded_at) - func.julianday(usage_with_prev.c.prev_at)) * 24 * 60
    jump_candidates = (
        select(
            usage_with_prev.c.account_id,
            usage_with_prev.c.prev_at,
            usage_with_prev.c.recorded_at.label("at"),
            delta_pp.label("delta_pp"),
        )
        .where(
            and_(
                usage_with_prev.c.prev_at.is_not(None),
                usage_with_prev.c.prev_used_percent.is_not(None),
                delta_pp >= _UNEXPLAINED_JUMP_MIN_DELTA_PP,
                gap_minutes >= _UNEXPLAINED_JUMP_MIN_GAP_MINUTES,
            )
        )
        .subquery()
    )

    has_logs_in_jump_interval = (
        select(1)
        .select_from(RequestLog)
        .where(
            and_(
                RequestLog.account_id == jump_candidates.c.account_id,
                RequestLog.requested_at >= jump_candidates.c.prev_at,
                RequestLog.requested_at < jump_candidates.c.at,
            )
        )
        .limit(1)
    )
    unexplained_jump_accounts_stmt = (
        select(jump_candidates.c.account_id).distinct().where(~has_logs_in_jump_interval.exists())
    )
    unexplained_rows = await session.execute(unexplained_jump_accounts_stmt)
    for row in unexplained_rows.all():
        excluded_account_ids.add(str(row.account_id))

    included_account_ids = [account_id for account_id in latest_by_account_id if account_id not in excluded_account_ids]
    if not included_account_ids:
        return []

    used_delta_pp_by_account: dict[str, float] = {}
    for account_id in included_account_ids:
        used_percent = float(latest_by_account_id[account_id][0])
        used_delta_pp_by_account[account_id] = max(0.0, min(100.0, used_percent))

    cost_stmt = (
        select(
            RequestLog.account_id.label("account_id"),
            RequestLog.model.label("model"),
            func.sum(func.coalesce(RequestLog.input_tokens, 0)).label("input_sum"),
            func.sum(func.coalesce(RequestLog.output_tokens, 0)).label("output_sum"),
            func.sum(func.coalesce(RequestLog.cached_input_tokens, 0)).label("cached_sum"),
            func.sum(func.coalesce(RequestLog.reasoning_tokens, 0)).label("reasoning_sum"),
        )
        .select_from(RequestLog)
        .join(latest_usage, latest_usage.c.account_id == RequestLog.account_id)
        .where(
            and_(
                RequestLog.account_id.in_(included_account_ids),
                RequestLog.requested_at >= cutover_dt,
                RequestLog.requested_at <= now,
            )
        )
        .group_by(RequestLog.account_id, RequestLog.model)
    )
    cost_rows = await session.execute(cost_stmt)
    cost_usd_by_account: dict[str, float] = {}
    for row in cost_rows.all():
        account_id = str(row.account_id)
        input_tokens = float(row.input_sum or 0)
        cached_tokens = float(row.cached_sum or 0)
        cached_tokens = max(0.0, min(cached_tokens, input_tokens))
        output_tokens = float(row.output_sum or 0)
        if output_tokens <= 0 and (row.reasoning_sum or 0) > 0:
            output_tokens = float(row.reasoning_sum or 0)

        resolved = get_pricing_for_model(str(row.model), None, None)
        if not resolved:
            continue
        _, price = resolved
        cost = calculate_cost_from_usage(
            UsageTokens(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_tokens,
            ),
            price,
        )
        if cost is None or cost < 0:
            continue
        cost_usd_by_account[account_id] = cost_usd_by_account.get(account_id, 0.0) + float(cost)

    observations: list[SecondaryQuotaEstimateObservation] = []
    for account_id in account_ids:
        cost_usd_7d = cost_usd_by_account.get(account_id)
        used_delta_pp_7d = used_delta_pp_by_account.get(account_id)
        if cost_usd_7d is None or used_delta_pp_7d is None:
            continue
        observations.append(
            SecondaryQuotaEstimateObservation(
                account_id=account_id,
                cost_usd_7d=cost_usd_7d,
                used_delta_pp_7d=used_delta_pp_7d,
            )
        )
    return observations
