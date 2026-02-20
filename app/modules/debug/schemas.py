from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DebugAccountRef(BaseModel):
    email: str
    account_id_short: str = Field(min_length=1, max_length=3)


class DebugUsageSnapshot(BaseModel):
    recorded_at: datetime | None
    used_percent: float | None
    reset_at: datetime | None
    window_minutes: int | None


class DebugEligibility(BaseModel):
    eligible: bool
    reason: str | None


class DebugLbAccountRow(BaseModel):
    email: str
    account_id_short: str = Field(min_length=1, max_length=3)
    plan_type: str
    status: str
    deactivation_reason: str | None

    db_reset_at: datetime | None
    selection_reset_at: datetime | None

    primary: DebugUsageSnapshot | None
    secondary: DebugUsageSnapshot | None

    cooldown_until: datetime | None
    last_error_at: datetime | None
    last_selected_at: datetime | None
    error_count: int

    pinned_pool: DebugEligibility
    full_pool: DebugEligibility

    sticky_count: int | None


class DebugLbStateResponse(BaseModel):
    server_time: datetime
    snapshot_updated_at: datetime
    sticky_backend: str
    pinned_accounts: list[DebugAccountRef]
    accounts: list[DebugLbAccountRow]


class DebugLbSelectionEvent(BaseModel):
    ts: datetime
    request_id: str | None
    pool: str
    sticky_backend: str
    reallocate_sticky: bool
    outcome: str
    reason_code: str | None
    selected: DebugAccountRef | None
    error_message: str | None
    fallback_from_pinned: bool


class DebugLbEventsResponse(BaseModel):
    server_time: datetime
    events: list[DebugLbSelectionEvent]
