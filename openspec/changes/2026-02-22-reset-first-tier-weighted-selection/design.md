# Design

## Overview

Selection is performed per account, using only the secondary reset deadline and a tier weight.

Key policy:

- **Eligibility** remains health/status based (paused/deactivated excluded, cooldown/backoff honored, blocked statuses
  excluded until reset).
- **Ranking** ignores usage/remaining credits and uses only `secondary_reset_at`:
  - within a tier this yields strict earliest-reset-first
  - across tiers it trades off earlier resets vs higher-tier preference

## Tier normalization

Normalize `plan_type` into tiers:

- `pro` -> `pro`
- `plus`, `team`, `business` -> `plus`
- `free` -> `free`
- unknown -> `plus`

## Tier weights (latency × quality)

Weights are a product of:

- latency-derived base weights (normalized to `pro=1.0`):
  - `plus=0.8`, `free=0.64` (given ~0.8x latency per tier step)
- a moderate quality preference multiplier:
  - `plus=0.9`, `free=0.8`

Final weights:

- `pro = 1.0`
- `plus = 0.72`
- `free = 0.512`

## Eligibility (unchanged)

Selection starts from the existing eligible account set:

- Exclude `PAUSED` and `DEACTIVATED`
- Exclude `RATE_LIMITED` / `QUOTA_EXCEEDED` until their `reset_at` passes
- Exclude accounts during cooldown/backoff windows
- Exclude secondary-exhausted accounts when:
  - `secondary_used_percent >= 100`, and
  - `secondary_reset_at` is known and in the future

## Ranking (per-account)

For each eligible account:

- If `secondary_reset_at` is known:
  - `time_to_reset = max(60, secondary_reset_at - now)`
  - `score = tier_weight(tier) / time_to_reset`
- If `secondary_reset_at` is unknown (`None`):
  - `score = 0`

Choose the account with maximum `score`.

### Tie-breaks (deterministic)

When two accounts have equal `score`:

1. earlier `secondary_reset_at` (known sorts before unknown)
2. higher `tier_weight` (prefer higher tier)
3. lexical `account_id`

### All scores are zero

If all eligible accounts have `secondary_reset_at=None` (all scores `0`), choose deterministically by:

1. higher `tier_weight`
2. lexical `account_id`

Rationale: missing reset timestamps are an observability/input issue and should not cause random behavior.

## Observability / trace

Selection traces continue to include:

- selected tier (derived from the selected account)
- per-tier score observations (for metrics/debugging)

Per-tier score should be defined as the best (maximum) per-account `score` within that tier; it is non-negative and
finite.

## Compatibility

- Stickiness behavior remains unchanged.
- Pinned pool behavior remains unchanged (pool applied before stickiness; fallback to full pool if pinned pool is empty).

