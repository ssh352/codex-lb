# Design

## Terminology

- **Waste pressure**: an estimate of how quickly secondary quota is likely to be wasted if it is
  not used before the next secondary reset.

## Configuration

Introduce an environment setting:

- `CODEX_LB_PROXY_SELECTION_STRATEGY`:
  - `usage` (default): current behavior (lowest usage percent).
  - `reset_bucket`: day-bucketed earlier secondary reset first (current `prefer_earlier_reset` logic).
  - `waste_pressure`: prefer highest waste pressure (new behavior).

To preserve current UI semantics, the dashboard setting `prefer_earlier_reset_accounts=True` maps to
`reset_bucket` **only** when `CODEX_LB_PROXY_SELECTION_STRATEGY=usage`.

## Waste pressure score

For each eligible account, compute:

- `secondary_capacity_credits`: plan-dependent capacity for the secondary window.
- `secondary_used_percent`: latest secondary usage percent (default 0 when unknown).
- `secondary_remaining_credits = secondary_capacity_credits * (1 - secondary_used_percent/100)`.
- `time_to_secondary_reset_seconds`:
  - If `secondary_reset_at` is known: `max(60, secondary_reset_at - now)`.
  - Otherwise: use the default secondary window length (7 days).
- `pressure = secondary_remaining_credits / time_to_secondary_reset_seconds`.

To avoid routing to accounts that are likely to trip a primary limit, apply a success weighting:

- `primary_headroom = max(0, 100 - primary_used_percent) / 100` (default 1.0 when unknown)
- `success_factor = primary_headroom^2`
- `health_factor = 1 / (1 + error_count)`

Final score:

- `waste_pressure_score = pressure * success_factor * health_factor`

Selection chooses the account with the maximum score, using existing usage sort keys for stable
tie-breaking.

## Stickiness interaction

Stickiness is still the primary routing constraint. The selection strategy only influences:

1. The initial sticky assignment for a new `prompt_cache_key`.
2. Reallocation when the pinned account becomes unavailable or explicit reallocation occurs.

## Risk

Medium. The change introduces a new selection mode; behavior remains unchanged unless explicitly
enabled via configuration.

