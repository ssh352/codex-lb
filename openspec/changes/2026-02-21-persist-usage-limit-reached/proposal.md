# Proposal: Persist upstream `usage_limit_reached` as a real rate-limit state

## Problem

When upstream returns HTTP `429` with `code=usage_limit_reached` and a concrete reset time (`resets_at` /
`resets_in_seconds`), codex-lb currently treats it as an in-memory cooldown only. The account remains `ACTIVE` in the
accounts table and can be re-selected after a process restart, causing:

- Dashboard confusion: “ACTIVE” while requests are blocked until reset.
- Avoidable retry churn: repeated selection of an account that cannot succeed until `resets_at`.
- Pinned-pool fragility: pinned accounts can look available while effectively unusable.

## Goals

- Persist the unusable period across restarts when upstream provides a concrete reset time.
- Align dashboard/account status with upstream semantics (blocked until reset).
- Keep behavior safe for ambiguous/transient `usage_limit_reached` responses that have no reset hints.

## Non-goals

- Changing upstream usage meter interpretation (`used_percent`) or recalculating credits/quota logic.
- Adding a new account status enum value (use existing `rate_limited`).
- Implementing an early “probe” mechanism to re-enable an account before `resets_at`.

