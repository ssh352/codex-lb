# Routing Pool (Pinned Accounts)

## Purpose

Provide an operator-controlled “routing pool” that can temporarily constrain proxy routing to a chosen subset of
accounts, without pausing/resuming every other account.

## Scope

- How pinned accounts are represented and applied during proxy account selection.
- How pinned state is surfaced via dashboard APIs for UI display.

## Decisions

- The pool is a **list** (not a single account) so operators can constrain routing while still balancing within the
  pool.
- When the pool becomes unusable (e.g. all pinned accounts are unavailable), routing **falls back** to normal
  selection across all accounts to avoid avoidable outages.

## How work is spread within the pool

When multiple accounts are pinned, routing is **not** round-robin.

- The proxy first filters eligible candidates to only pinned accounts.
- It then ranks eligible accounts using a reset-first, tier-weighted score:
  - if `secondary_reset_at` is known: `score = tier_weight / max(60, secondary_reset_at - now)`
  - if `secondary_reset_at` is unknown: `score = 0`
  - `tier_weight` defaults to `pro=1.00`, `plus=0.72`, `free=0.512` (latency + quality preference)
- Selection is deterministic (stable tie-breaks) and, within a tier, behaves as strict “earliest secondary reset first”.
- Stickiness can still keep a given `prompt_cache_key` on the same pinned account until a retry reallocates it (or the
  pinned account becomes ineligible). It does not proactively migrate just because another account later has higher
  selector score.
- Stickiness never overrides the routing pool: if a sticky mapping points to an unpinned account while the pool is
  active, the proxy drops that mapping and reassigns the key within the pinned pool.

### Why tier-weighted reset-first (not waste-pressure)

Operators often reason about accounts in terms of their next secondary reset boundary ("which one resets first?").
This selector makes that the primary signal, while still expressing a preference for higher tiers across the pool.

Compared to waste-pressure (`remaining_secondary_credits / time_to_reset`), reset-first has simpler behavior and makes
same-tier selection intuitive. The tradeoff is that it can leave secondary credits unused at reset boundaries, because
remaining balance is not part of the ranking.

Tier weights encode a product preference (latency + probable response-quality advantage) without needing per-request
token/cost prediction.

## Failure Modes

- If the pinned pool contains only unavailable accounts, routing will fall back to normal selection (see `spec.md`).
- If an account becomes `quota_exceeded`, the system prunes it from the pinned pool so the dashboard pin state clears.
- If dashboard settings are corrupted (invalid JSON), settings access should fail fast so operators can correct the
  stored value.

## Example

1) Operator selects accounts `acc_a` and `acc_b` in the dashboard and adds them to the routing pool.
2) Proxy routing only selects between `acc_a` and `acc_b` while either is available.
3) If both `acc_a` and `acc_b` become unavailable (paused / deactivated / limited), routing falls back to normal
   selection across all accounts.
