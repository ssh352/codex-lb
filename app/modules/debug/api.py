from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config.settings import get_settings
from app.dependencies import ProxyContext, get_proxy_context
from app.modules.debug.schemas import (
    DebugAccountRef,
    DebugEligibility,
    DebugLbAccountRow,
    DebugLbEventsResponse,
    DebugLbSelectionEvent,
    DebugLbStateResponse,
    DebugUsageSnapshot,
)

router = APIRouter(tags=["debug"], include_in_schema=False)

UTC = timezone.utc


def _short_account_id(value: str) -> str:
    raw = (value or "").strip()
    return raw[:3] if raw else ""


def _dt_from_epoch(value: float | int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=UTC)


@router.get("/debug/lb/state", response_model=DebugLbStateResponse)
async def debug_lb_state(
    ctx: ProxyContext = Depends(get_proxy_context),
) -> DebugLbStateResponse:
    if not get_settings().debug_endpoints_enabled:
        raise HTTPException(status_code=404)

    dump = await ctx.service.debug_lb_dump()
    pinned_accounts: list[DebugAccountRef] = []
    for account_id in dump.pinned_account_ids:
        account = dump.accounts_by_id.get(account_id)
        if account is None:
            continue
        pinned_accounts.append(
            DebugAccountRef(
                email=account.email,
                account_id_short=_short_account_id(account_id),
            )
        )

    accounts: list[DebugLbAccountRow] = []
    for row in dump.account_rows:
        primary = dump.latest_primary.get(row.account_id)
        secondary = dump.latest_secondary.get(row.account_id)
        pinned_reason = dump.pinned_ineligibility.get(row.account_id)
        full_reason = dump.full_ineligibility.get(row.account_id)
        sticky_count = dump.sticky_counts.get(row.account_id)

        accounts.append(
            DebugLbAccountRow(
                email=row.email,
                account_id_short=_short_account_id(row.account_id),
                plan_type=row.plan_type,
                status=row.status,
                deactivation_reason=row.deactivation_reason,
                db_reset_at=_dt_from_epoch(row.db_reset_at),
                selection_reset_at=_dt_from_epoch(dump.selection_reset_at.get(row.account_id)),
                primary=(
                    DebugUsageSnapshot(
                        recorded_at=primary.recorded_at,
                        used_percent=primary.used_percent,
                        reset_at=_dt_from_epoch(primary.reset_at),
                        window_minutes=primary.window_minutes,
                    )
                    if primary is not None
                    else None
                ),
                secondary=(
                    DebugUsageSnapshot(
                        recorded_at=secondary.recorded_at,
                        used_percent=secondary.used_percent,
                        reset_at=_dt_from_epoch(secondary.reset_at),
                        window_minutes=secondary.window_minutes,
                    )
                    if secondary is not None
                    else None
                ),
                cooldown_until=_dt_from_epoch(dump.cooldown_until.get(row.account_id)),
                last_error_at=_dt_from_epoch(dump.last_error_at.get(row.account_id)),
                last_selected_at=_dt_from_epoch(dump.last_selected_at.get(row.account_id)),
                error_count=dump.error_count.get(row.account_id, 0),
                pinned_pool=DebugEligibility(eligible=pinned_reason is None, reason=pinned_reason),
                full_pool=DebugEligibility(eligible=full_reason is None, reason=full_reason),
                sticky_count=sticky_count,
            )
        )

    server_time = datetime.now(tz=UTC)
    return DebugLbStateResponse(
        server_time=server_time,
        snapshot_updated_at=_dt_from_epoch(dump.snapshot_updated_at_seconds) or server_time,
        sticky_backend=dump.sticky_backend,
        pinned_accounts=pinned_accounts,
        accounts=accounts,
    )


@router.get("/debug/lb/events", response_model=DebugLbEventsResponse)
async def debug_lb_events(
    ctx: ProxyContext = Depends(get_proxy_context),
    limit: int = Query(default=200, ge=1),
) -> DebugLbEventsResponse:
    if not get_settings().debug_endpoints_enabled:
        raise HTTPException(status_code=404)

    dump = await ctx.service.debug_lb_dump()
    events_raw = ctx.service.debug_lb_events(limit=limit)

    events: list[DebugLbSelectionEvent] = []
    for ev in events_raw:
        selected_ref: DebugAccountRef | None = None
        if ev.selected_account_id:
            account = dump.accounts_by_id.get(ev.selected_account_id)
            if account is not None:
                selected_ref = DebugAccountRef(
                    email=account.email,
                    account_id_short=_short_account_id(ev.selected_account_id),
                )
        events.append(
            DebugLbSelectionEvent(
                ts=_dt_from_epoch(ev.ts_epoch) or datetime.now(tz=UTC),
                request_id=ev.request_id,
                pool=ev.pool,
                sticky_backend=ev.sticky_backend,
                reallocate_sticky=ev.reallocate_sticky,
                outcome=ev.outcome,
                reason_code=ev.reason_code,
                selected=selected_ref,
                error_message=ev.error_message,
                fallback_from_pinned=ev.fallback_from_pinned,
            )
        )

    return DebugLbEventsResponse(
        server_time=datetime.now(tz=UTC),
        events=events,
    )
