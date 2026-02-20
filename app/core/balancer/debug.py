from __future__ import annotations

import time

from app.core.balancer.logic import AccountState
from app.db.models import AccountStatus


def ineligibility_reason(
    state: AccountState,
    *,
    now: float | None = None,
) -> str | None:
    current = now if now is not None else time.time()

    if state.status == AccountStatus.DEACTIVATED:
        return "deactivated"
    if state.status == AccountStatus.PAUSED:
        return "paused"

    if state.status == AccountStatus.RATE_LIMITED:
        if state.reset_at and current >= state.reset_at:
            pass
        else:
            return "rate_limited"

    if state.status == AccountStatus.QUOTA_EXCEEDED:
        if state.reset_at and current >= state.reset_at:
            pass
        else:
            return "quota_exceeded"

    if state.cooldown_until and current < state.cooldown_until:
        return "cooldown"

    if state.error_count >= 3:
        backoff = min(300.0, 30.0 * (2.0 ** float(state.error_count - 3)))
        if state.last_error_at and current - state.last_error_at < backoff:
            return "error_backoff"

    return None
