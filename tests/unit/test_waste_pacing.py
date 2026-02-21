from __future__ import annotations

import pytest

from app.core.usage.waste_pacing import (
    SecondaryWastePacingInput,
    compute_secondary_waste_pacing,
)

pytestmark = pytest.mark.unit


def test_secondary_waste_pacing_on_track_when_current_rate_matches_required_rate() -> None:
    # 60-minute window, 30 minutes elapsed, 30 minutes remaining.
    # If used == remaining, then current rate == required rate and projected waste == 0.
    result = compute_secondary_waste_pacing(
        [
            SecondaryWastePacingInput(
                account_id="acc_a",
                plan_type="plus",
                secondary_used_percent=50.0,
                secondary_reset_at_epoch=3600,
                secondary_window_minutes=60,
            )
        ],
        now_epoch=1800,
    )

    assert result.summary.accounts_evaluated == 1
    assert result.summary.accounts_at_risk == 0
    assert result.summary.projected_waste_credits_total == pytest.approx(0.0)
    assert result.summary.current_rate_credits_per_hour_total == pytest.approx(7560.0)
    assert result.summary.required_rate_credits_per_hour_total == pytest.approx(7560.0)

    account = result.accounts[0]
    assert account.on_track is True
    assert account.projected_waste_credits == pytest.approx(0.0)
    assert account.current_rate_credits_per_hour == pytest.approx(7560.0)
    assert account.required_rate_credits_per_hour == pytest.approx(7560.0)


def test_secondary_waste_pacing_current_rate_is_unknown_when_elapsed_is_zero() -> None:
    # At the very start of the window: elapsed == 0 => current rate is unknown.
    result = compute_secondary_waste_pacing(
        [
            SecondaryWastePacingInput(
                account_id="acc_a",
                plan_type="plus",
                secondary_used_percent=10.0,
                secondary_reset_at_epoch=3600,
                secondary_window_minutes=60,
            )
        ],
        now_epoch=0,
    )
    account = result.accounts[0]
    assert account.current_rate_credits_per_hour is None
    assert account.projected_waste_credits is None
    assert account.on_track is None
    assert result.summary.accounts_evaluated == 0


def test_secondary_waste_pacing_requires_reset_timestamp() -> None:
    result = compute_secondary_waste_pacing(
        [
            SecondaryWastePacingInput(
                account_id="acc_a",
                plan_type="plus",
                secondary_used_percent=10.0,
                secondary_reset_at_epoch=None,
                secondary_window_minutes=60,
            )
        ],
        now_epoch=0,
    )
    account = result.accounts[0]
    assert account.current_rate_credits_per_hour is None
    assert account.required_rate_credits_per_hour is None
    assert account.projected_waste_credits is None
    assert account.on_track is None


def test_secondary_waste_pacing_unknown_capacity_produces_null_rates() -> None:
    result = compute_secondary_waste_pacing(
        [
            SecondaryWastePacingInput(
                account_id="acc_unknown",
                plan_type="unknown",
                secondary_used_percent=10.0,
                secondary_reset_at_epoch=3600,
                secondary_window_minutes=60,
            )
        ],
        now_epoch=1800,
    )
    account = result.accounts[0]
    assert account.remaining_credits_secondary is None
    assert account.current_rate_credits_per_hour is None
    assert account.required_rate_credits_per_hour is None
    assert account.projected_waste_credits is None
    assert account.on_track is None
