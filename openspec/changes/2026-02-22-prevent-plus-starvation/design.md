# Design

## Overview

Keep the two-stage hybrid selector shape, but change how cross-tier urgency is computed so large tiers cannot starve
smaller tiers purely due to account count.

Additionally, treat accounts whose secondary window is exhausted (`secondary_used_percent >= 100`) as ineligible for
selection until their secondary reset, even if persisted status is still `ACTIVE`.

## Selection algorithm

Selection starts from the existing eligible account set (paused/deactivated excluded, cooldown/backoff honored,
limited/exceeded blocked until reset).

### Step 0: secondary exhaustion gating

If an account has:

- `secondary_used_percent >= 100`, and
- `secondary_reset_at` is known and in the future,

then the account is treated as ineligible for routing until its secondary reset boundary.

Rationale: this avoids repeatedly selecting an account that is guaranteed to fail with quota errors and prevents
“earliest reset first” from picking fully exhausted accounts just because their reset time is earlier.

### Step 1: tier normalization (unchanged)

Normalize account `plan_type` into tiers:

- `pro` -> `pro`
- `plus`, `team`, `business` -> `plus`
- `free` -> `free`
- unknown -> `plus`

### Step 2: per-account urgency (unchanged core signal)

For each eligible account:

- `secondary_used = secondary_used_percent ?? used_percent ?? 0`
- `remaining_secondary_credits = secondary_capacity_credits * max(0, 100-secondary_used) / 100`
- `time_to_reset = max(60, secondary_reset_at - now)` (if `secondary_reset_at` known)
- `required_rate = remaining_secondary_credits / time_to_reset`

If reset/capacity is unknown, `required_rate = 0`.

### Step 3: per-tier score (changed aggregation)

For each tier:

- `tier_required_rate = max(required_rate for eligible accounts in tier)`
- `tier_score = tier_required_rate * tier_latency_weight`

Latency weights remain:

- `pro = 1.00`
- `plus = 0.95`
- `free = 0.90`

Choose the tier with max `tier_score`.

Tie-break order:

1. earlier minimum `secondary_reset_at` in tier (known before unknown)
2. larger total remaining secondary credits (informational; does not scale selection by tier size)
3. lexical tier name

### Step 4: intra-tier account selection (changed)

Within the selected tier, pick the account with the highest `required_rate`.

Tie-break order:

1. earlier `secondary_reset_at` (`None` sorts last)
2. lower secondary-used-percent (fallback to primary-used)
3. older `last_selected_at`
4. lexical `account_id`

### Step 5: fallback (unchanged)

If all tiers score zero, fall back to deterministic usage ordering.

## Debug surfacing

Selection debug trace MUST remain decision-complete. The trace should continue to include:

- selected tier
- per-tier urgency inputs and resulting score
- selected account urgency inputs

Optional (recommended): add a stable marker in the trace for the tier aggregation mode (e.g. `"max"`).

## Compatibility

- Stickiness behavior remains unchanged.
- `accounts.reset_at` durable blocked-until semantics remain unchanged.
- Pinned-pool behavior remains unchanged (pool applied before stickiness; fallback to full pool if pinned pool is empty).
