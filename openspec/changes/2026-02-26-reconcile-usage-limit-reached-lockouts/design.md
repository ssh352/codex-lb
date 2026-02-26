# Design

## Grounding (observations from local data)

From `~/.codex-lb/store.db` over the last 48 hours (captured on 2026-02-26):

- `usage_limit_reached` events: 199.
- `usage_limit_reached` message shape is uniform: “The usage limit has been reached” (no “try again in …” hint).
- After a `usage_limit_reached` error *ends* (request end time):
  - next request on the same account starts within 10s in 112/199 (56%)
  - within 60s in 134/199 (67%)
- Among comparable `usage_limit_reached` pairs on the same account (has a previous `usage_limit_reached` in-window),
  104/127 (82%) repeat within 60s.
- At the time of a `usage_limit_reached` event, the latest secondary usage snapshot at/before the event is:
  - < 100% in 162/198 (82%)
  - >= 100% in 36/198 (18%)

Interpretation: current “generic rate limit” cooldown behavior is often too short for `usage_limit_reached` with no
reset hints, producing rapid re-selection loops. However, since immediate success is also observed after some
`usage_limit_reached` events, treating every `usage_limit_reached` as a hard weekly lockout would be too aggressive.

## Current behavior (what exists today)

- `usage_limit_reached` is classified as a retryable/rate-limit-like error.
- When upstream does not include a usable retry hint:
  - codex-lb uses exponential backoff starting at ~0.2s (attempt 1), doubling thereafter.
  - the effective “blocked until” boundary is often seconds-scale, making the account eligible again quickly.

## Proposed policy (decision)

Introduce a dedicated handler for upstream `usage_limit_reached` that is still “rate-limit-like”, but:

### 1) Minimum cooldown when no reset hint exists (anti-thrash)

If upstream provides **no** `resets_at`/`resets_in_seconds` and the error message does not include a parseable retry
delay, enforce a minimum cooldown:

- `cooldown = max(min_cooldown_seconds, backoff_seconds(error_count))`

Rationale: avoid “seconds-scale” re-selection loops while preserving eventual recovery.

### 2) Capped initial lockout when upstream provides a far reset hint (anti false-positive long locks)

If upstream provides a concrete reset hint (`resets_at` or `resets_in_seconds`) and the implied delay is “long”:

- On the first couple of occurrences in a streak, cap the cooldown to `max_initial_cooldown_seconds` (default 5 minutes)
  rather than immediately persisting an hours/days-long `reset_at`.
- Escalate to the full upstream reset boundary only with evidence:
  - repeated `usage_limit_reached` within a streak (e.g. `error_count >= 3` for this code), OR
  - local secondary exhaustion is confirmed (`secondary_used_percent >= 100` with a known secondary reset).

Rationale: upstream reset hints are valuable, but single-event long lockouts have been observed to be false positives.

### 3) Weekly exhaustion remains telemetry-derived

Do not map `usage_limit_reached` directly to `quota_exceeded`. Weekly exhaustion continues to be derived from
`usage_history.secondary.used_percent >= 100` (and should override the last upstream error code).

## Configuration

Add settings (env vars) with defaults:

- `CODEX_LB_USAGE_LIMIT_REACHED_MIN_COOLDOWN_SECONDS` (default: 60)
- `CODEX_LB_USAGE_LIMIT_REACHED_MAX_INITIAL_COOLDOWN_SECONDS` (default: 300)
- `CODEX_LB_USAGE_LIMIT_REACHED_ESCALATE_STREAK_THRESHOLD` (default: 3)
- `CODEX_LB_USAGE_LIMIT_REACHED_PERSIST_RESET_THRESHOLD_SECONDS` (default: 300)

Notes:
- The “persist threshold” avoids persisting reset boundaries that are only a few seconds/minutes away.
- The “escalate threshold” is code-local for `usage_limit_reached` (not the global error streak).

## Acceptance criteria

- With no upstream reset hint, `usage_limit_reached` never produces sub-second cooldowns (min cooldown enforced).
- With upstream reset hints:
  - A single `usage_limit_reached` does not immediately persist a multi-hour/day `reset_at`.
  - Repeated `usage_limit_reached` within a streak can persist the upstream reset boundary.
- Weekly exhaustion continues to surface as `quota_exceeded` derived from telemetry regardless of upstream errors.

