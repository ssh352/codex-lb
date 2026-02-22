# Dashboard Account Status: Blocked Convergence

## Requirements

- Dashboard account listing endpoints MUST NOT return accounts in a blocked status (`rate_limited` or `quota_exceeded`)
  when the effective “blocked until” timestamp is in the past.
- When an account is persisted as blocked (`accounts.status` in `{rate_limited, quota_exceeded}`), dashboard endpoints
  MUST compute an effective “blocked until” boundary as the latest (`max`) of:
  - the persisted blocked boundary (`accounts.reset_at`) when present, and
  - the relevant usage window reset boundary (`usage_history.reset_at`) when present
    (`rate_limited` → primary; `quota_exceeded` → secondary).
- If the effective “blocked until” boundary is known and is `<= now`, the system MUST clear the persisted blocked state
  before returning dashboard responses by setting:
  - `accounts.status = active`
  - `accounts.reset_at = null`
- Dashboard APIs MUST expose the effective “blocked until” timestamp as an ISO 8601 `datetime` field (`statusResetAt`)
  or `null` when unavailable.

