from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.core import usage as usage_core


@dataclass(frozen=True, slots=True)
class SecondaryWastePacingInput:
    account_id: str
    plan_type: str
    secondary_used_percent: float | None
    secondary_reset_at_epoch: int | None
    secondary_window_minutes: int | None


@dataclass(frozen=True, slots=True)
class SecondaryWastePacingAccount:
    account_id: str
    secondary_reset_at_epoch: int | None
    remaining_credits_secondary: float | None
    current_rate_credits_per_hour: float | None
    required_rate_credits_per_hour: float | None
    projected_waste_credits: float | None
    on_track: bool | None


@dataclass(frozen=True, slots=True)
class SecondaryWastePacingSummary:
    accounts_evaluated: int
    accounts_at_risk: int
    projected_waste_credits_total: float
    current_rate_credits_per_hour_total: float | None
    required_rate_credits_per_hour_total: float | None


@dataclass(frozen=True, slots=True)
class SecondaryWastePacingResult:
    summary: SecondaryWastePacingSummary
    accounts: list[SecondaryWastePacingAccount]


def compute_secondary_waste_pacing(
    inputs: Sequence[SecondaryWastePacingInput],
    *,
    now_epoch: int,
) -> SecondaryWastePacingResult:
    accounts: list[SecondaryWastePacingAccount] = []
    evaluated = 0
    at_risk = 0
    projected_waste_total = 0.0
    current_rate_total: float | None = None
    required_rate_total: float | None = None

    for item in inputs:
        capacity = usage_core.capacity_for_plan(item.plan_type, "secondary")
        used_percent = float(item.secondary_used_percent or 0.0)
        used_credits = usage_core.used_credits_from_percent(used_percent, capacity) if capacity is not None else None
        remaining_credits = (
            usage_core.remaining_credits_from_used(used_credits, capacity) if capacity is not None else None
        )

        reset_at = item.secondary_reset_at_epoch
        if reset_at is not None and reset_at <= 0:
            reset_at = None

        window_minutes = item.secondary_window_minutes
        if window_minutes is None or window_minutes <= 0:
            window_minutes = usage_core.default_window_minutes("secondary")

        window_len_s = int(window_minutes * 60) if window_minutes is not None else None
        time_to_reset_s = max(0, int(reset_at) - int(now_epoch)) if reset_at is not None else None
        elapsed_s = (
            max(0, int(window_len_s) - int(time_to_reset_s))
            if window_len_s is not None and time_to_reset_s is not None
            else None
        )

        current_rate: float | None = None
        if elapsed_s is not None and elapsed_s > 0 and used_credits is not None:
            current_rate = (float(used_credits) / float(elapsed_s)) * 3600.0

        required_rate: float | None = None
        if time_to_reset_s is not None and time_to_reset_s > 0 and remaining_credits is not None:
            required_rate = (float(remaining_credits) / float(time_to_reset_s)) * 3600.0

        projected_waste: float | None = None
        on_track: bool | None = None
        if current_rate is not None and remaining_credits is not None and time_to_reset_s is not None:
            projected_waste = max(
                0.0,
                float(remaining_credits) - ((float(current_rate) / 3600.0) * float(time_to_reset_s)),
            )
            on_track = projected_waste <= 0.5

        if projected_waste is not None:
            evaluated += 1
            projected_waste_total += float(projected_waste)
            if projected_waste > 0.5:
                at_risk += 1

        if current_rate is not None:
            current_rate_total = (current_rate_total or 0.0) + float(current_rate)

        if required_rate is not None:
            required_rate_total = (required_rate_total or 0.0) + float(required_rate)

        accounts.append(
            SecondaryWastePacingAccount(
                account_id=item.account_id,
                secondary_reset_at_epoch=reset_at,
                remaining_credits_secondary=float(remaining_credits) if remaining_credits is not None else None,
                current_rate_credits_per_hour=float(current_rate) if current_rate is not None else None,
                required_rate_credits_per_hour=float(required_rate) if required_rate is not None else None,
                projected_waste_credits=float(projected_waste) if projected_waste is not None else None,
                on_track=on_track,
            )
        )

    return SecondaryWastePacingResult(
        summary=SecondaryWastePacingSummary(
            accounts_evaluated=evaluated,
            accounts_at_risk=at_risk,
            projected_waste_credits_total=float(projected_waste_total),
            current_rate_credits_per_hour_total=current_rate_total,
            required_rate_credits_per_hour_total=required_rate_total,
        ),
        accounts=accounts,
    )
