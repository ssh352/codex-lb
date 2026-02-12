# Design

## Single algorithm

Account selection always uses a single score-based algorithm that maximizes an estimate of secondary
quota waste avoidance.

For each eligible account:

- `secondary_capacity_credits`: plan-dependent capacity for the secondary window.
- `secondary_used_percent`:
  - If known: use it.
  - If unknown: approximate with `primary_used_percent` (conservative; avoids treating unknown as 0%).
- `secondary_remaining_credits = secondary_capacity_credits * (1 - secondary_used_percent/100)`.
- `time_to_secondary_reset_seconds`:
  - If `secondary_reset_at` is known: `max(60, secondary_reset_at - now)`.
  - If unknown: the account is **ineligible** for waste-pressure scoring (`pressure = 0`).
- `pressure = secondary_remaining_credits / time_to_secondary_reset_seconds`.

Apply success/health weighting:

- `primary_headroom = max(0, 100 - primary_used_percent) / 100` (default 1.0 when unknown)
- `success_factor = primary_headroom^2`
- `health_factor = 1 / (1 + error_count)`

Final score:

- `waste_pressure_score = pressure * success_factor * health_factor`

Selection chooses the account with the maximum score, using existing usage sort keys for stable
tie-breaking when scores are equal.

## Removed surfaces

- Remove `CODEX_LB_PROXY_SELECTION_STRATEGY`.
- Remove `reset_bucket` and `usage` strategy code paths.
- Remove the dashboard toggle `prefer_earlier_reset_accounts` from the public settings API/UI (DB
  column may remain for backwards compatibility).

