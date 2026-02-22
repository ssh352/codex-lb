from __future__ import annotations

import pytest

from app.core.balancer import (
    AccountState,
    handle_permanent_failure,
    handle_quota_exceeded,
    handle_rate_limit,
    handle_usage_limit_reached,
    select_account,
)
from app.core.usage.quota import apply_usage_quota
from app.db.models import AccountStatus

pytestmark = pytest.mark.unit


def test_select_account_picks_lowest_used_percent():
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=50.0),
        AccountState("b", AccountStatus.ACTIVE, used_percent=10.0),
    ]
    result = select_account(states)
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_within_tier_uses_strict_earliest_secondary_reset():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "earlier",
            AccountStatus.ACTIVE,
            plan_type="free",
            secondary_used_percent=99.0,
            secondary_reset_at=int(now + 600),
            secondary_capacity_credits=1000.0,
        ),
        AccountState(
            "later",
            AccountStatus.ACTIVE,
            plan_type="free",
            secondary_used_percent=0.0,
            secondary_reset_at=int(now + 3600),
            secondary_capacity_credits=1000.0,
        ),
    ]
    result = select_account(states, now=now)
    assert result.account is not None
    assert result.account.account_id == "earlier"


def test_select_account_cross_tier_prefers_weighted_urgency():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "free_early",
            AccountStatus.ACTIVE,
            plan_type="free",
            secondary_used_percent=99.0,
            secondary_reset_at=int(now + 3600),
            secondary_capacity_credits=1000.0,
        ),
        AccountState(
            "plus_later",
            AccountStatus.ACTIVE,
            plan_type="plus",
            secondary_used_percent=0.0,
            secondary_reset_at=int(now + 7200),
            secondary_capacity_credits=1000.0,
        ),
    ]
    result = select_account(states, now=now)
    assert result.account is not None
    assert result.account.account_id == "plus_later"


def test_select_account_cross_tier_applies_latency_weight_on_close_urgency():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "plus_candidate",
            AccountStatus.ACTIVE,
            plan_type="plus",
            secondary_used_percent=0.0,
            secondary_reset_at=int(now + 3600),
            secondary_capacity_credits=1000.0,
        ),
        AccountState(
            "pro_candidate",
            AccountStatus.ACTIVE,
            plan_type="pro",
            secondary_used_percent=0.0,
            secondary_reset_at=int(now + 3600),
            secondary_capacity_credits=1000.0,
        ),
    ]
    result = select_account(states, now=now)
    assert result.account is not None
    assert result.account.account_id == "pro_candidate"


def test_select_account_prefers_known_reset_inside_selected_tier():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "unknown_reset",
            AccountStatus.ACTIVE,
            plan_type="plus",
            secondary_used_percent=0.0,
            secondary_reset_at=None,
            secondary_capacity_credits=1000.0,
        ),
        AccountState(
            "known_reset",
            AccountStatus.ACTIVE,
            plan_type="plus",
            secondary_used_percent=0.0,
            secondary_reset_at=int(now + 3600),
            secondary_capacity_credits=1000.0,
        ),
    ]
    result = select_account(states, now=now)
    assert result.account is not None
    assert result.account.account_id == "known_reset"


def test_select_account_falls_back_to_usage_sort_when_all_tier_scores_unknown():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "higher_used",
            AccountStatus.ACTIVE,
            plan_type="plus",
            used_percent=70.0,
            secondary_used_percent=70.0,
            secondary_reset_at=None,
            secondary_capacity_credits=None,
        ),
        AccountState(
            "lower_used",
            AccountStatus.ACTIVE,
            plan_type="free",
            used_percent=10.0,
            secondary_used_percent=10.0,
            secondary_reset_at=None,
            secondary_capacity_credits=None,
        ),
    ]
    result = select_account(states, now=now)
    assert result.account is not None
    assert result.account.account_id == "lower_used"


def test_select_account_emits_tier_trace_fields():
    now = 1_700_000_000.0
    states = [
        AccountState(
            "free_1",
            AccountStatus.ACTIVE,
            plan_type="free",
            secondary_used_percent=0.0,
            secondary_reset_at=int(now + 600),
            secondary_capacity_credits=1000.0,
        ),
        AccountState(
            "plus_1",
            AccountStatus.ACTIVE,
            plan_type="plus",
            secondary_used_percent=0.0,
            secondary_reset_at=int(now + 3600),
            secondary_capacity_credits=1000.0,
        ),
    ]
    result = select_account(states, now=now)
    assert result.account is not None
    assert result.trace is not None
    assert result.trace.selected_tier in {"free", "plus", "pro"}
    assert result.trace.tier_scores


def test_select_account_skips_rate_limited_until_reset():
    now = 1_700_000_000.0
    states = [
        AccountState("a", AccountStatus.RATE_LIMITED, used_percent=5.0, reset_at=int(now + 60)),
        AccountState("b", AccountStatus.ACTIVE, used_percent=10.0),
    ]
    result = select_account(states, now=now)
    assert result.account is not None
    assert result.account.account_id == "b"


def test_handle_rate_limit_sets_reset_at_from_message(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.core.balancer.logic.time.time", lambda: now)
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_rate_limit(state, {"message": "Try again in 1.5s"})
    assert state.status == AccountStatus.RATE_LIMITED
    assert state.cooldown_until is not None
    assert state.cooldown_until == pytest.approx(now + 1.5)


def test_handle_rate_limit_uses_backoff_when_no_delay(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.core.balancer.logic.time.time", lambda: now)
    monkeypatch.setattr("app.core.balancer.logic.backoff_seconds", lambda _: 0.2)
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_rate_limit(state, {"message": "Rate limit exceeded."})
    assert state.status == AccountStatus.RATE_LIMITED
    assert state.cooldown_until is not None
    assert state.cooldown_until == pytest.approx(now + 0.2)


def test_handle_usage_limit_reached_caps_first_long_reset(monkeypatch):
    now = 1_700_000_000.0
    reset_at = int(now + 3600)
    monkeypatch.setattr("app.core.balancer.logic.time.time", lambda: now)
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_usage_limit_reached(
        state,
        {
            "message": "The usage limit has been reached",
            "resets_at": reset_at,
            "resets_in_seconds": 3600,
        },
    )
    assert state.status == AccountStatus.RATE_LIMITED
    assert state.cooldown_until is not None
    assert state.cooldown_until == pytest.approx(now + 300.0)
    assert state.reset_at == pytest.approx(now + 300.0)


def test_handle_usage_limit_reached_escalates_after_repeated_errors(monkeypatch):
    now = 1_700_000_000.0
    reset_at = int(now + 3600)
    monkeypatch.setattr("app.core.balancer.logic.time.time", lambda: now)
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0, error_count=2)
    handle_usage_limit_reached(
        state,
        {
            "message": "The usage limit has been reached",
            "resets_at": reset_at,
            "resets_in_seconds": 3600,
        },
    )
    assert state.status == AccountStatus.RATE_LIMITED
    assert state.reset_at == pytest.approx(float(reset_at))
    assert state.cooldown_until is not None
    assert state.cooldown_until == pytest.approx(now + 300.0)


def test_handle_usage_limit_reached_marks_rate_limited_without_reset_hints(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.core.balancer.logic.time.time", lambda: now)
    monkeypatch.setattr("app.core.balancer.logic.backoff_seconds", lambda _: 0.25)
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_usage_limit_reached(state, {"message": "The usage limit has been reached"})
    assert state.status == AccountStatus.RATE_LIMITED
    assert state.reset_at is not None
    assert state.cooldown_until is not None
    assert state.cooldown_until == pytest.approx(now + 0.25)
    assert state.reset_at == pytest.approx(now + 0.25)


def test_handle_usage_limit_reached_marks_rate_limited_for_short_reset(monkeypatch):
    now = 1_700_000_000.0
    reset_at = int(now + 30)
    monkeypatch.setattr("app.core.balancer.logic.time.time", lambda: now)
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_usage_limit_reached(state, {"resets_at": reset_at, "resets_in_seconds": 30})
    assert state.status == AccountStatus.RATE_LIMITED
    assert state.reset_at is not None
    assert state.cooldown_until is not None
    assert state.cooldown_until == pytest.approx(now + 30.0)
    assert state.reset_at == pytest.approx(now + 30.0)


def test_handle_usage_limit_reached_escalates_when_secondary_exhausted(monkeypatch):
    now = 1_700_000_000.0
    reset_at = int(now + 3600)
    monkeypatch.setattr("app.core.balancer.logic.time.time", lambda: now)
    state = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=5.0,
        secondary_used_percent=100.0,
        secondary_reset_at=reset_at,
    )
    handle_usage_limit_reached(
        state,
        {
            "message": "The usage limit has been reached",
            "resets_at": reset_at,
            "resets_in_seconds": 3600,
        },
    )
    assert state.status == AccountStatus.RATE_LIMITED
    assert state.reset_at == pytest.approx(float(reset_at))
    assert state.cooldown_until is not None
    assert state.cooldown_until == pytest.approx(now + 300.0)


def test_handle_usage_limit_reached_persists_when_message_has_long_delay(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.core.balancer.logic.time.time", lambda: now)
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_usage_limit_reached(state, {"message": "The usage limit has been reached. Try again in 3600s"})
    assert state.status == AccountStatus.RATE_LIMITED
    assert state.reset_at == pytest.approx(now + 300.0)
    assert state.cooldown_until is not None
    assert state.cooldown_until == pytest.approx(now + 300.0)


def test_select_account_skips_cooldown_until_expired():
    now = 1_700_000_000.0
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=5.0, cooldown_until=now + 60),
        AccountState("b", AccountStatus.ACTIVE, used_percent=10.0),
    ]
    result = select_account(states, now=now)
    assert result.account is not None
    assert result.account.account_id == "b"


def test_select_account_resets_error_count_when_cooldown_expires():
    now = 1_700_000_000.0
    state = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=5.0,
        cooldown_until=now - 1,
        last_error_at=now - 10,
        error_count=4,
    )
    result = select_account([state], now=now)
    assert result.account is not None
    assert state.cooldown_until is None
    assert state.last_error_at is None
    assert state.error_count == 0


def test_select_account_reports_cooldown_wait_time():
    now = 1_700_000_000.0
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=5.0, cooldown_until=now + 30),
        AccountState("b", AccountStatus.ACTIVE, used_percent=10.0, cooldown_until=now + 60),
    ]
    result = select_account(states, now=now)
    assert result.account is None
    assert result.error_message is not None
    assert "Try again in" in result.error_message


def test_apply_usage_quota_sets_fallback_reset_for_primary_window(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.ACTIVE,
        primary_used=100.0,
        primary_reset=None,
        primary_window_minutes=1,
        runtime_reset=None,
        secondary_used=None,
        secondary_reset=None,
    )
    assert status == AccountStatus.RATE_LIMITED
    assert used_percent == 100.0
    assert reset_at is not None
    assert reset_at == pytest.approx(now + 60.0)


def test_handle_quota_exceeded_sets_used_percent():
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_quota_exceeded(state, {})
    assert state.status == AccountStatus.QUOTA_EXCEEDED
    assert state.used_percent == 100.0


def test_handle_permanent_failure_sets_reason():
    state = AccountState("a", AccountStatus.ACTIVE, used_percent=5.0)
    handle_permanent_failure(state, "refresh_token_expired")
    assert state.status == AccountStatus.DEACTIVATED
    assert state.deactivation_reason is not None


def test_apply_usage_quota_respects_runtime_reset_for_quota_exceeded(monkeypatch):
    now = 1_700_000_000.0
    future = now + 3600.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    # Normally 50% used would reset it to ACTIVE, but runtime_reset is in future
    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.QUOTA_EXCEEDED,
        primary_used=50.0,
        primary_reset=None,
        primary_window_minutes=None,
        runtime_reset=future,
        secondary_used=None,
        secondary_reset=None,
    )
    assert status == AccountStatus.QUOTA_EXCEEDED
    assert used_percent == 50.0
    assert reset_at == future


def test_apply_usage_quota_respects_runtime_reset_for_rate_limited(monkeypatch):
    now = 1_700_000_000.0
    future = now + 3600.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    # Normally 50% used would reset it to ACTIVE, but runtime_reset is in future
    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.RATE_LIMITED,
        primary_used=50.0,
        primary_reset=None,
        primary_window_minutes=None,
        runtime_reset=future,
        secondary_used=None,
        secondary_reset=None,
    )
    assert status == AccountStatus.RATE_LIMITED
    assert used_percent == 50.0
    assert reset_at == future


def test_apply_usage_quota_resets_to_active_if_runtime_reset_expired(monkeypatch):
    now = 1_700_000_000.0
    past = now - 3600.0
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)

    status, used_percent, reset_at = apply_usage_quota(
        status=AccountStatus.RATE_LIMITED,
        primary_used=50.0,
        primary_reset=None,
        primary_window_minutes=None,
        runtime_reset=past,
        secondary_used=None,
        secondary_reset=None,
    )
    assert status == AccountStatus.ACTIVE
    assert used_percent == 50.0
    assert reset_at is None
