from __future__ import annotations

from app.db.models import Account, AccountStatus, UsageHistory


def stale_blocked_account_ids(
    *,
    accounts: list[Account],
    primary_usage: dict[str, UsageHistory],
    secondary_usage: dict[str, UsageHistory],
    now_epoch: int,
) -> set[str]:
    stale_ids: set[str] = set()
    for account in accounts:
        usage_reset_at: int | None = None
        if account.status == AccountStatus.RATE_LIMITED:
            usage_entry = primary_usage.get(account.id)
            usage_reset_at = usage_entry.reset_at if usage_entry is not None else None
        elif account.status == AccountStatus.QUOTA_EXCEEDED:
            usage_entry = secondary_usage.get(account.id)
            usage_reset_at = usage_entry.reset_at if usage_entry is not None else None
        else:
            continue

        candidates = [value for value in (account.reset_at, usage_reset_at) if value is not None]
        if not candidates:
            continue
        status_reset_at = max(candidates)
        if status_reset_at <= now_epoch:
            stale_ids.add(account.id)
    return stale_ids
