# Proposal: Deterministic forced-account routing + safer `usage_limit_reached` lockouts

## Problem

1) Operators sometimes need to observe the *actual* upstream behavior of a specific ChatGPT account (e.g. why an account
   is failing) but codex-lb’s normal behavior can mask it:
   - pinned routing is a pool preference and can fall back to the full pool when pinned candidates are unavailable
   - the proxy can fail over across accounts on retryable upstream errors

2) Upstream `usage_limit_reached` can be transient and may not match the weekly usage meter, but codex-lb may lock an
   account out until a far reset boundary if the upstream error includes `resets_at`/`resets_in_seconds`.

## Goals

- Provide a deterministic, per-request mechanism to route to a specific account for debugging.
- Reduce false-positive multi-hour/day lockouts from a single `usage_limit_reached`.
- Preserve the ability to persist a long lockout when there is stronger evidence (repeated errors or weekly exhaustion).

## Non-goals

- Reworking the dashboard UI/UX beyond minimally surfacing the underlying `accounts.reset_at` ("blocked until") state.
- Adding new account status enum values (reuse `rate_limited`).
- Making network calls to `/wham/usage` on the proxy hot path to confirm weekly exhaustion.
