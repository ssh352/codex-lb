# Design

## Policy

When upstream returns `429` with `code=usage_limit_reached`:

- If the error payload includes a concrete reset hint (`resets_at` or `resets_in_seconds`):
  - Treat it as a real limit until the reset boundary.
  - Persist the account as `rate_limited` with `accounts.reset_at` set to the upstream reset time.
  - Ensure the load balancer’s `cooldown_until` is at least the reset time.
- If there are no reset hints:
  - Keep existing “soft cooldown” behavior (short backoff) and do not persist a multi-hour lock.

## Avoiding “temporary” usage limits

To reduce false positives where upstream may emit `usage_limit_reached` transiently, persist `rate_limited` only when:

- The reset time is present **and**
- The computed delay-to-reset is at/above a small threshold (recommended default: `>= 5 minutes`).

Rationale: short-lived limits are fine to represent as in-memory backoff; a long reset boundary should be treated as a
real “unusable until reset” state so it survives restarts and is clearly visible.

## Implementation sketch

- Update `app/core/balancer/logic.py::handle_usage_limit_reached()`:
  - Parse `resets_at`/`resets_in_seconds`.
  - Compute `delay_to_reset`.
  - Set `cooldown_until = max(existing_delay, delay_to_reset)`.
  - If reset hint exists and `delay_to_reset >= threshold`, set:
    - `state.status = AccountStatus.RATE_LIMITED`
    - `state.reset_at = float(resets_at_epoch)`
- No API schema changes required: the existing `Account.status` + `reset_at` will reflect the lockout.

## Example (observed)

For a free-tier account, upstream returned:

- HTTP `429`
- `code=usage_limit_reached`
- `resets_at=1771729714` (2026-02-22 03:08:34 UTC)

This should be persisted as `rate_limited` until that reset time even if the usage meter’s `used_percent` sample is
below 100%.
