# Proposal: Tier-aware hybrid account selection

Current routing uses a single per-account waste-pressure score. This can be hard to reason about in mixed plan-type
pools where operators expect intuitive same-tier behavior.

This change adopts a two-stage hybrid selector:

1. Cross-tier selection: choose the plan-type tier with the highest aggregate urgency multiplied by a mild latency
   weight.
2. Intra-tier selection: choose the earliest secondary reset account (strict ERF) among eligible accounts in that tier.

Goals:

- Keep same-tier behavior intuitive (earliest reset first).
- Preserve waste minimization across tiers.
- Include mild latency preference (`pro > plus > free`) without dominating urgency.

Non-goals:

- No stickiness migration changes (still no proactive migration on reset boundaries).
- No persistence model changes for blocked status (`accounts.reset_at` remains).
- No feature flag rollout; this is a direct cutover.
