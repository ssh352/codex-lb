# Proposal: Reconcile `usage_limit_reached` lockouts (avoid thrash, avoid false long locks)

## Problem

Codex-lb currently treats upstream `usage_limit_reached` as rate-limit-like and applies short exponential backoff when
upstream does not provide a retry hint. In practice, this can cause rapid retry loops (same account reselected seconds
after a `usage_limit_reached`) and unnecessary upstream load / noisy failures.

Separately, upstream sometimes provides a far `resets_at` boundary for `usage_limit_reached` that may be transient /
misleading for a single event; persisting a multi-hour/day lockout immediately can create false-positive “unusable until
reset” states.

## Goals

- Stop “seconds-scale” re-selection loops after `usage_limit_reached` when upstream provides no usable retry hint.
- Preserve the existing semantic split:
  - `usage_limit_reached` remains rate-limit-like for status (not immediately `quota_exceeded`).
  - Weekly exhaustion remains a *local* derivation from the secondary usage meter (`used_percent >= 100`).
- Escalate to long lockouts only with evidence (reset hints and/or repeated error streaks), not on a single ambiguous
  event.

## Non-goals

- Introducing per-account concurrency limits (no evidence-based need yet).
- Changing client behavior; this is load-balancer policy only.

