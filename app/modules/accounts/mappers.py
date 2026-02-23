from __future__ import annotations

from datetime import datetime, timezone

from app.core import usage as usage_core
from app.core.auth import DEFAULT_PLAN, extract_id_token_claims
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.utils.time import from_epoch_seconds
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts.schemas import AccountAuthStatus, AccountSummary, AccountTokenStatus, AccountUsage


def build_account_summaries(
    *,
    accounts: list[Account],
    primary_usage: dict[str, UsageHistory],
    secondary_usage: dict[str, UsageHistory],
    encryptor: TokenEncryptor,
    pinned_account_ids: set[str] | None = None,
) -> list[AccountSummary]:
    pinned = pinned_account_ids or set()
    return [
        _account_to_summary(
            account,
            primary_usage.get(account.id),
            secondary_usage.get(account.id),
            encryptor,
            pinned,
        )
        for account in accounts
    ]


def _account_to_summary(
    account: Account,
    primary_usage: UsageHistory | None,
    secondary_usage: UsageHistory | None,
    encryptor: TokenEncryptor,
    pinned_account_ids: set[str],
) -> AccountSummary:
    plan_type = coerce_account_plan_type(account.plan_type, DEFAULT_PLAN)
    auth_status = _build_auth_status(account, encryptor)
    primary_used_percent = _normalize_used_percent(primary_usage) or 0.0
    secondary_used_percent = _normalize_used_percent(secondary_usage) or 0.0
    primary_remaining_percent = usage_core.remaining_percent_from_used(primary_used_percent) or 0.0
    secondary_remaining_percent = usage_core.remaining_percent_from_used(secondary_used_percent) or 0.0
    reset_at_primary = from_epoch_seconds(primary_usage.reset_at) if primary_usage is not None else None
    reset_at_secondary = from_epoch_seconds(secondary_usage.reset_at) if secondary_usage is not None else None

    # `status_reset_at` is the dashboard-facing "blocked until" timestamp: the earliest time codex-lb
    # expects this account to become selectable for routing again.
    #
    # It intentionally does *not* mean "when the usage window resets" (that's `reset_at_primary` /
    # `reset_at_secondary`). In many real cases it will still match the secondary reset, because:
    # - `quota_exceeded` is defined by weekly exhaustion, and
    # - upstream `usage_limit_reached` may report a reset boundary equal to the weekly reset.
    #
    # Clarification: this is derived from `accounts.reset_at` (persisted, durable "blocked until")
    # and the latest usage reset timestamps. It is only meaningful for blocked statuses
    # (RATE_LIMITED / QUOTA_EXCEEDED). If an account is ACTIVE, any stored reset timestamp should
    # be treated as stale and ignored.
    #
    # Note: a long `status_reset_at` (sometimes matching the weekly reset boundary) does not imply
    # the account status must be `quota_exceeded`. For example, upstream `usage_limit_reached` is
    # persisted as `rate_limited` by policy, but the upstream-provided reset hint (or usage reset)
    # may still align with the secondary reset time.
    status_reset_seconds: int | None = None
    if account.status == AccountStatus.RATE_LIMITED:
        usage_reset_seconds = primary_usage.reset_at if primary_usage is not None else None
        candidates = [entry for entry in (account.reset_at, usage_reset_seconds) if entry is not None]
        status_reset_seconds = max(candidates) if candidates else None
    elif account.status == AccountStatus.QUOTA_EXCEEDED:
        usage_reset_seconds = secondary_usage.reset_at if secondary_usage is not None else None
        candidates = [entry for entry in (account.reset_at, usage_reset_seconds) if entry is not None]
        status_reset_seconds = max(candidates) if candidates else None
    status_reset_at = from_epoch_seconds(status_reset_seconds) if status_reset_seconds is not None else None
    capacity_primary = usage_core.capacity_for_plan(plan_type, "primary")
    capacity_secondary = usage_core.capacity_for_plan(plan_type, "secondary")
    remaining_credits_primary = usage_core.remaining_credits_from_percent(
        primary_used_percent,
        capacity_primary,
    )
    remaining_credits_secondary = usage_core.remaining_credits_from_percent(
        secondary_used_percent,
        capacity_secondary,
    )
    return AccountSummary(
        account_id=account.id,
        email=account.email,
        display_name=account.email,
        plan_type=plan_type,
        status=account.status.value,
        status_reset_at=status_reset_at,
        pinned=account.id in pinned_account_ids,
        usage=AccountUsage(
            primary_remaining_percent=primary_remaining_percent,
            secondary_remaining_percent=secondary_remaining_percent,
        ),
        reset_at_primary=reset_at_primary,
        reset_at_secondary=reset_at_secondary,
        last_refresh_at=account.last_refresh,
        capacity_credits_primary=capacity_primary,
        remaining_credits_primary=remaining_credits_primary,
        capacity_credits_secondary=capacity_secondary,
        remaining_credits_secondary=remaining_credits_secondary,
        deactivation_reason=account.deactivation_reason,
        auth=auth_status,
    )


def _build_auth_status(account: Account, encryptor: TokenEncryptor) -> AccountAuthStatus:
    access_token = _decrypt_token(encryptor, account.access_token_encrypted)
    refresh_token = _decrypt_token(encryptor, account.refresh_token_encrypted)
    id_token = _decrypt_token(encryptor, account.id_token_encrypted)

    access_expires = _token_expiry(access_token)
    refresh_state = "stored" if refresh_token else "missing"
    id_state = "unknown"
    if id_token:
        claims = extract_id_token_claims(id_token)
        if claims.model_dump(exclude_none=True):
            id_state = "parsed"

    return AccountAuthStatus(
        access=AccountTokenStatus(expires_at=access_expires),
        refresh=AccountTokenStatus(state=refresh_state),
        id_token=AccountTokenStatus(state=id_state),
    )


def _decrypt_token(encryptor: TokenEncryptor, encrypted: bytes | None) -> str | None:
    if not encrypted:
        return None
    try:
        return encryptor.decrypt(encrypted)
    except Exception:
        return None


def _token_expiry(token: str | None) -> datetime | None:
    if not token:
        return None
    claims = extract_id_token_claims(token)
    exp = claims.exp
    if isinstance(exp, (int, float)):
        return datetime.fromtimestamp(exp, tz=timezone.utc)
    if isinstance(exp, str) and exp.isdigit():
        return datetime.fromtimestamp(int(exp), tz=timezone.utc)
    return None


def _normalize_used_percent(entry: UsageHistory | None) -> float | None:
    if not entry:
        return None
    return entry.used_percent
