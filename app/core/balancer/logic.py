from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from app.core.balancer.types import UpstreamError
from app.core.utils.retry import backoff_seconds, parse_retry_after
from app.db.models import AccountStatus

PERMANENT_FAILURE_CODES = {
    "refresh_token_expired": "Refresh token expired - re-login required",
    "refresh_token_reused": "Refresh token was reused - re-login required",
    "refresh_token_invalidated": "Refresh token was revoked - re-login required",
    "account_suspended": "Account has been suspended",
    "account_deleted": "Account has been deleted",
}

_USAGE_LIMIT_REACHED_PERSIST_THRESHOLD_SECONDS = 5 * 60.0
_USAGE_LIMIT_REACHED_INITIAL_COOLDOWN_CAP_SECONDS = 5 * 60.0
_USAGE_LIMIT_REACHED_ESCALATE_AFTER_ERRORS = 3


@dataclass
class AccountState:
    account_id: str
    status: AccountStatus
    used_percent: float | None = None
    reset_at: float | None = None
    cooldown_until: float | None = None
    secondary_used_percent: float | None = None
    secondary_reset_at: int | None = None
    secondary_capacity_credits: float | None = None
    last_error_at: float | None = None
    last_selected_at: float | None = None
    error_count: int = 0
    deactivation_reason: str | None = None


@dataclass
class SelectionResult:
    account: AccountState | None
    error_message: str | None
    reason_code: str | None


def select_account(
    states: Iterable[AccountState],
    now: float | None = None,
) -> SelectionResult:
    current = now or time.time()
    available: list[AccountState] = []
    all_states = list(states)

    for state in all_states:
        if state.status == AccountStatus.DEACTIVATED:
            continue
        if state.status == AccountStatus.PAUSED:
            continue
        if state.status == AccountStatus.RATE_LIMITED:
            if state.reset_at and current >= state.reset_at:
                state.status = AccountStatus.ACTIVE
                state.error_count = 0
                state.reset_at = None
            else:
                continue
        if state.status == AccountStatus.QUOTA_EXCEEDED:
            if state.reset_at and current >= state.reset_at:
                state.status = AccountStatus.ACTIVE
                state.used_percent = 0.0
                state.reset_at = None
            else:
                continue
        if state.cooldown_until and current >= state.cooldown_until:
            state.cooldown_until = None
            state.last_error_at = None
            state.error_count = 0
        if state.cooldown_until and current < state.cooldown_until:
            continue
        if state.error_count >= 3:
            backoff = min(300, 30 * (2 ** (state.error_count - 3)))
            if state.last_error_at and current - state.last_error_at < backoff:
                continue
        available.append(state)

    if not available:
        deactivated = [s for s in all_states if s.status == AccountStatus.DEACTIVATED]
        paused = [s for s in all_states if s.status == AccountStatus.PAUSED]
        rate_limited = [s for s in all_states if s.status == AccountStatus.RATE_LIMITED]
        quota_exceeded = [s for s in all_states if s.status == AccountStatus.QUOTA_EXCEEDED]

        if paused and deactivated and not rate_limited and not quota_exceeded:
            return SelectionResult(None, "All accounts are paused or require re-authentication", "paused_or_auth")
        if paused and not rate_limited and not quota_exceeded:
            return SelectionResult(None, "All accounts are paused", "paused")
        if deactivated and not rate_limited and not quota_exceeded:
            return SelectionResult(None, "All accounts require re-authentication", "auth")
        if rate_limited:
            reset_candidates = [s.reset_at for s in rate_limited if s.reset_at]
            if reset_candidates:
                wait_seconds = max(0, min(reset_candidates) - int(current))
                return SelectionResult(None, f"Rate limit exceeded. Try again in {wait_seconds:.0f}s", "rate_limited")
        if quota_exceeded:
            reset_candidates = [s.reset_at for s in quota_exceeded if s.reset_at]
            if reset_candidates:
                wait_seconds = max(0, min(reset_candidates) - int(current))
                return SelectionResult(None, f"Rate limit exceeded. Try again in {wait_seconds:.0f}s", "quota_exceeded")
        cooldowns = [s.cooldown_until for s in all_states if s.cooldown_until and s.cooldown_until > current]
        if cooldowns:
            wait_seconds = max(0.0, min(cooldowns) - current)
            return SelectionResult(None, f"Rate limit exceeded. Try again in {wait_seconds:.0f}s", "cooldown")
        return SelectionResult(None, "No available accounts", "no_available")

    def _usage_sort_key(state: AccountState) -> tuple[float, float, float, str]:
        primary_used = state.used_percent if state.used_percent is not None else 0.0
        secondary_used = state.secondary_used_percent if state.secondary_used_percent is not None else primary_used
        # Prefer accounts that were selected less recently (helps spread work across similarly-scored accounts).
        last_selected = state.last_selected_at or 0.0
        return secondary_used, primary_used, last_selected, state.account_id

    # Waste-pressure selection:
    # Prefer accounts whose unused secondary credits are most likely to be wasted soon
    # (secondary_remaining / time_to_secondary_reset), while down-weighting accounts that are likely
    # to fail due to low primary headroom or recent errors.
    #
    # Rationale (important with stickiness): a "earliest reset first" strategy is deadline-only and
    # can pin long-lived sticky traffic to an account that resets soon; once it resets, it remains
    # eligible and keeps serving that sticky key, while other accounts' secondary windows expire
    # unused because we don't proactively migrate stickiness on reset events.
    def _waste_pressure_sort_key(state: AccountState) -> tuple[float, float, float, float, str]:
        primary_used = float(state.used_percent or 0.0)
        secondary_capacity = float(state.secondary_capacity_credits or 0.0)
        secondary_used = (
            float(state.secondary_used_percent) if state.secondary_used_percent is not None else float(primary_used)
        )
        secondary_remaining = secondary_capacity * max(0.0, 100.0 - secondary_used) / 100.0

        if state.secondary_reset_at is None:
            pressure = 0.0
        else:
            time_to_reset = max(60.0, float(state.secondary_reset_at) - current)
            pressure = (secondary_remaining / time_to_reset) if secondary_remaining > 0.0 else 0.0

        primary_headroom = max(0.0, 100.0 - primary_used) / 100.0
        success_factor = primary_headroom**2
        health_factor = 1.0 / (1.0 + float(max(0, state.error_count)))

        score = pressure * success_factor * health_factor
        secondary_used_fallback, primary_used_fallback, last_selected, account_id = _usage_sort_key(state)
        # Use negative score so min() selects the maximum score.
        return -score, secondary_used_fallback, primary_used_fallback, last_selected, account_id

    selected = min(available, key=_waste_pressure_sort_key)
    return SelectionResult(selected, None, None)


def handle_rate_limit(state: AccountState, error: UpstreamError) -> None:
    state.status = AccountStatus.RATE_LIMITED
    state.error_count += 1
    state.last_error_at = time.time()

    reset_at = _extract_reset_at(error)
    if reset_at is not None:
        state.reset_at = reset_at

    message = error.get("message")
    delay = parse_retry_after(message) if message else None
    if delay is None:
        delay = backoff_seconds(state.error_count)
    state.cooldown_until = time.time() + delay
    # Fail-safe: if upstream did not provide a reset time, ensure we can recover automatically.
    # `select_account()` only auto-clears RATE_LIMITED when `reset_at` is set and in the past.
    if state.reset_at is None:
        state.reset_at = float(state.cooldown_until)


def handle_usage_limit_reached(state: AccountState, error: UpstreamError) -> None:
    # Upstream `usage_limit_reached` indicates the account is not currently usable. It can be
    # transient and may not correlate with the weekly quota being fully exhausted, but if we keep
    # the account "ACTIVE" we end up re-selecting it and repeating failures, and the dashboard is
    # misleading.
    #
    # Policy:
    # - always apply a retry delay (prefer explicit retry hints; otherwise backoff)
    # - mark the account RATE_LIMITED until the computed delay expires
    # - if upstream provides a stable reset boundary far enough in the future, prefer that *only*
    #   after we have stronger evidence this is not transient.
    #
    # Note: upstream sometimes reports a reset boundary that matches the account's weekly (secondary)
    # window reset. When we escalate to that boundary, the account's "blocked until" time will line
    # up with the dashboard's 7d quota reset time. That's expected: it means upstream is effectively
    # saying "this account is unusable until the secondary window resets".
    state.error_count += 1
    now = time.time()
    state.last_error_at = now

    message = error.get("message")
    delay = parse_retry_after(message) if message else None
    reset_at_raw = _extract_reset_at(error)

    reset_boundary_epoch: float | None = float(reset_at_raw) if reset_at_raw is not None else None
    if delay is None:
        reset_in = error.get("resets_in_seconds")
        if isinstance(reset_in, (int, float)) and reset_in > 0:
            delay = float(reset_in)
            if reset_boundary_epoch is None:
                reset_boundary_epoch = now + float(reset_in)

    delay_to_reset: float | None = None
    if reset_boundary_epoch is not None:
        delay_to_reset = max(0.0, float(reset_boundary_epoch) - now)
        delay = max(float(delay or 0.0), delay_to_reset)

    if delay is None:
        delay = backoff_seconds(state.error_count)

    # `usage_limit_reached` often comes with two timing signals:
    # - a *reset boundary* (`resets_at` / `resets_in_seconds`) indicating when upstream expects the
    #   account to be usable again, and
    # - an *effective retry delay* we impose (`cooldown_until`) to back off after errors.
    #
    # We keep BOTH `cooldown_until` and `reset_at`:
    # - `cooldown_until` is a short-term backoff hint (mostly about not hammering upstream).
    # - `reset_at` is the durable "blocked until" gate used by selection logic and dashboard state.
    #
    # Key nuance for `usage_limit_reached`:
    # - Upstream reset boundaries can be overly conservative (we've observed `usage_limit_reached`
    #   followed by successful requests well before the reported reset time).
    # - Therefore, for long reset hints we *initially* persist only a short durable lock
    #   (`reset_at == cooldown_until`) so the account is retried soon.
    # - We only escalate `reset_at` to the upstream reset boundary once we have stronger evidence
    #   this isn't transient (repeated errors in a streak or weekly window exhaustion).
    #
    # NOTE: `reset_at` should be meaningful only for blocked statuses (RATE_LIMITED/QUOTA_EXCEEDED);
    # having `status=ACTIVE` with a non-null `reset_at` is a stale/inconsistent state.
    #
    # For debugging, codex-lb currently persists only the normalized error code/message in request
    # logs (not the full upstream error payload), so relying on the upstream-provided reset hints
    # at the time of marking is important when we DO escalate.
    capped_delay = min(float(delay), float(_USAGE_LIMIT_REACHED_INITIAL_COOLDOWN_CAP_SECONDS))
    state.cooldown_until = now + capped_delay
    state.status = AccountStatus.RATE_LIMITED
    if delay_to_reset is None or delay_to_reset < _USAGE_LIMIT_REACHED_PERSIST_THRESHOLD_SECONDS:
        state.reset_at = float(state.cooldown_until)
        return

    secondary_exhausted = (
        state.secondary_used_percent is not None
        and state.secondary_used_percent >= 100.0
        and state.secondary_reset_at is not None
    )
    repeated_errors = state.error_count >= _USAGE_LIMIT_REACHED_ESCALATE_AFTER_ERRORS

    if secondary_exhausted or repeated_errors:
        if reset_boundary_epoch is not None:
            state.reset_at = float(reset_boundary_epoch)
        else:
            state.reset_at = float(state.cooldown_until)
        return

    # First long reset hint: treat as potentially transient. Keep a short durable lock (reset_at ==
    # cooldown_until) so the account is retried soon, while still backing off for the next attempt.
    state.reset_at = float(state.cooldown_until)


def handle_quota_exceeded(state: AccountState, error: UpstreamError) -> None:
    state.status = AccountStatus.QUOTA_EXCEEDED
    state.used_percent = 100.0

    reset_at = _extract_reset_at(error)
    if reset_at is not None:
        state.reset_at = reset_at
    else:
        state.reset_at = int(time.time() + 3600)


def handle_permanent_failure(state: AccountState, error_code: str) -> None:
    state.status = AccountStatus.DEACTIVATED
    state.deactivation_reason = PERMANENT_FAILURE_CODES.get(
        error_code,
        f"Authentication failed: {error_code}",
    )


def _extract_reset_at(error: UpstreamError) -> int | None:
    reset_at = error.get("resets_at")
    if reset_at is not None:
        return int(reset_at)
    reset_in = error.get("resets_in_seconds")
    if reset_in is not None:
        return int(time.time() + float(reset_in))
    return None
