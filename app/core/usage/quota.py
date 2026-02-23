from __future__ import annotations

import time

from app.core import usage as usage_core
from app.db.models import AccountStatus


def apply_usage_quota(
    *,
    status: AccountStatus,
    primary_used: float | None,
    primary_reset: int | None,
    primary_window_minutes: int | None,
    runtime_reset: float | None,
    secondary_used: float | None,
    secondary_reset: int | None,
) -> tuple[AccountStatus, float | None, float | None]:
    used_percent = primary_used
    reset_at = runtime_reset

    if status in (AccountStatus.DEACTIVATED, AccountStatus.PAUSED):
        return status, used_percent, reset_at

    if secondary_used is not None:
        if secondary_used >= 100.0:
            # Weekly (secondary window) exhaustion is a *local* derivation from `usage_history`
            # and forces `QUOTA_EXCEEDED`, regardless of the last upstream error code/message.
            #
            # This is intentionally distinct from upstream "usage limit reached" signals, which
            # are treated as rate-limit-like and typically persist as `RATE_LIMITED` unless the
            # usage meter confirms exhaustion.
            status = AccountStatus.QUOTA_EXCEEDED
            used_percent = 100.0
            if secondary_reset is not None:
                reset_at = secondary_reset
            return status, used_percent, reset_at
        if status == AccountStatus.QUOTA_EXCEEDED:
            if runtime_reset and runtime_reset > time.time():
                reset_at = runtime_reset
            else:
                status = AccountStatus.ACTIVE
                reset_at = None
    elif status == AccountStatus.QUOTA_EXCEEDED and secondary_reset is not None:
        reset_at = secondary_reset

    if primary_used is not None:
        if primary_used >= 100.0:
            status = AccountStatus.RATE_LIMITED
            used_percent = 100.0
            if primary_reset is not None:
                reset_at = primary_reset
            else:
                reset_at = _fallback_primary_reset(primary_window_minutes) or reset_at
            return status, used_percent, reset_at
        if status == AccountStatus.RATE_LIMITED:
            if runtime_reset and runtime_reset > time.time():
                reset_at = runtime_reset
            else:
                status = AccountStatus.ACTIVE
                reset_at = None

    # `reset_at` is a persisted "blocked until" hint and should only be meaningful for blocked
    # statuses. Returning a non-null reset timestamp while `status=ACTIVE` creates an inconsistent
    # state in the accounts DB (and can confuse operators when debugging).
    if status == AccountStatus.ACTIVE:
        reset_at = None

    return status, used_percent, reset_at


def _fallback_primary_reset(primary_window_minutes: int | None) -> float | None:
    window_minutes = primary_window_minutes or usage_core.default_window_minutes("primary")
    if not window_minutes:
        return None
    return time.time() + float(window_minutes) * 60.0
