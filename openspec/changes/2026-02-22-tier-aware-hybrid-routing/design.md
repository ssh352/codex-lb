# Design

## Selection algorithm

Selection starts from the existing eligible account set (paused/deactivated excluded, cooldown/backoff honored,
limited/exceeded blocked until reset).

### Step 1: tier normalization

Normalize account `plan_type` into tiers:

- `pro` -> `pro`
- `plus`, `team`, `business` -> `plus`
- `free` -> `free`
- unknown -> `plus`

### Step 2: per-account urgency

For each eligible account:

- `secondary_used = secondary_used_percent ?? used_percent ?? 0`
- `remaining_secondary_credits = secondary_capacity_credits * max(0, 100-secondary_used) / 100`
- `time_to_reset = max(60, secondary_reset_at - now)` (if `secondary_reset_at` known)
- `required_rate = remaining_secondary_credits / time_to_reset`

If reset/capacity is unknown, `required_rate = 0`.

### Step 3: per-tier score

For each tier:

- `aggregate_required_rate = sum(required_rate for accounts in tier)`
- `tier_score = aggregate_required_rate * tier_latency_weight`

Latency weights:

- `pro = 1.00`
- `plus = 0.95`
- `free = 0.90`

Choose the tier with max `tier_score`.

Tie-break order:

1. earlier minimum `secondary_reset_at` in tier
2. larger total remaining secondary credits
3. lexical tier name

### Step 4: intra-tier account selection

Within the selected tier, pick account by:

1. earliest `secondary_reset_at` (`None` sorts last)
2. lower secondary-used-percent (fallback to primary-used)
3. older `last_selected_at`
4. lexical `account_id`

### Step 5: fallback

If all tiers score zero, fall back to deterministic usage ordering.

## Debug surfacing

Selection debug events include full decision trace:

- selected tier
- per-tier urgency, weight, score
- per-tier min reset and remaining credits
- selected account secondary reset / secondary used / primary used

## Compatibility

- Stickiness behavior remains unchanged.
- `accounts.reset_at` durable blocked-until semantics remain unchanged.
