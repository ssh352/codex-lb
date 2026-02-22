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
- It then applies the existing **hybrid** selector to that subset:
  - choose a plan-type tier by urgency (`sum(remaining_secondary_credits / time_to_reset)`) multiplied by a mild
    latency weight (`pro=1.00`, `plus=0.95`, `free=0.90`)
  - then choose an account within that tier using strict earliest secondary reset first (ERF)
- The selector includes a “last selected” tie-break, which tends to spread traffic across similarly-scored accounts.
- Stickiness can still keep a given `prompt_cache_key` on the same pinned account until a retry reallocates it (or the
  pinned account becomes ineligible). It does not proactively migrate just because another account later has higher
  selector score.
- Stickiness never overrides the routing pool: if a sticky mapping points to an unpinned account while the pool is
  active, the proxy drops that mapping and reassigns the key within the pinned pool.

### Why hybrid (not pure “earliest reset first” globally)

Global “earliest secondary reset first” is intuitive but deadline-only. It can under-utilize tiers with larger
remaining balances that reset slightly later, and it still interacts poorly with sticky routing:

- If a `prompt_cache_key` is assigned to an account that resets soon, that account will often reset **before** it ever
  becomes unavailable (it becomes *more* usable after reset).
- Because stickiness is honored as long as the pinned account remains eligible, the proxy will keep sending that
  key to the same account after its reset, instead of migrating to other accounts whose secondary credits are still in
  the pre-reset window.
- The result can be avoidable waste: other accounts’ secondary windows can expire with large unused balances simply
  because the sticky traffic never moved.

The hybrid selector keeps the intuitive ERF rule **within the chosen tier**, while using cross-tier urgency plus
latency weighting to avoid starving higher-impact remaining balances.

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
