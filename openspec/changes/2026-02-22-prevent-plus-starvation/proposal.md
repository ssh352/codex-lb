# Proposal: Prevent Plus starvation under tier-aware routing

## Problem

The current tier-aware hybrid selector scores plan-type tiers using an aggregate urgency of:

- `aggregate_required_rate = sum(remaining_secondary_credits / time_to_secondary_reset)` across eligible accounts in the tier

This scales with tier size. In real mixed pools with many `free` accounts, the `free` tier can dominate selection even
when one or more `plus` accounts are at risk of wasting secondary (weekly) credits before their reset.

Operators want:

- Plus accounts should not be perpetually starved by a large Free pool.
- If a Free account is *more urgent* (earlier reset with meaningful remaining secondary credits), routing should still
  be able to choose Free.

## Goals

- Prevent tier-size dilution: tier selection should reflect urgency, not pool cardinality.
- Keep selection deterministic and explainable (no randomness required).
- Preserve pinned-pool and stickiness semantics.

## Non-goals

- Stickiness migration changes (no proactive movement of sticky keys).
- Predicting per-request token usage / cost.
- Introducing new dashboard strategy toggles.
