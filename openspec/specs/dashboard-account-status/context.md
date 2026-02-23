## Dashboard Account Status: Blocked Convergence (Context)

### Problem

Codex-lb can persist an account as blocked (`rate_limited` / `quota_exceeded`) with a “blocked until” boundary
(`accounts.reset_at`). Routing can clear this state in-memory after the boundary passes, but without an explicit
write-back, the dashboard can continue to show confusing UI like “Blocked · Try again now”.

### Approach

Dashboard endpoints reconcile persisted state:

- compute the effective “blocked until” time as `max(accounts.reset_at, usage_history.reset_at)` for the relevant
  window, and
- if that time is in the past, clear the persisted blocked status so the account appears **Active**.

This makes the dashboard reflect real eligibility without requiring an operator to click **Resume**.

### Blocked status sources (upstream vs derived)

The account status shown in the dashboard is not always a direct reflection of the most recent upstream error code.

- **Upstream error codes (saved in request logs)** are raw signals, e.g.:
  - rate-limit-like: `rate_limit_exceeded`, `usage_limit_reached`
  - quota-like: `quota_exceeded`, `insufficient_quota`, `usage_not_included`
- **codex-lb status (`accounts.status`)** is a policy + derived-state classification:
  - `rate_limited` is commonly persisted for upstream `usage_limit_reached` so codex-lb backs off and retries.
    - This matches upstream behavior: `usage_limit_reached` is treated as rate-limit-like (not quota-exceeded).
    - The persisted “blocked until” boundary SHOULD come from upstream reset hints (`resets_at` /
      `resets_in_seconds`) when available.
  - `quota_exceeded` is set when:
    - upstream sends an explicit quota-like error code, and/or
    - the local weekly usage meter (secondary window) reaches exhaustion (`usage_history(window=secondary).used_percent >= 100`).

This means it is expected to see:
- the most recent upstream error message saying “usage limit reached”, while
- the account status shown as **Quota exceeded** (because the weekly usage meter is exhausted).

### Example

If an account is stored as:

- `accounts.status = rate_limited`
- `accounts.reset_at = 2026-02-21T15:50:50Z`

and the current time is after `2026-02-21T15:50:50Z`, the dashboard will clear the persisted status to `active` and stop
showing any “blocked” meta.

### Operator debugging: which account did a Codex request use?

If Codex shows a generic upstream message (e.g. “usage limit reached”) and you need to identify the specific account:

1. Open `http://127.0.0.1:2455/dashboard`
2. In **Recent requests**, the **Account** column is the selected account email, and **Error** shows the raw saved
   upstream error message (hover / click **More** to expand).

For immediate load-balancer selection history (even if a request wasn’t persisted), enable debug endpoints:

- Set `CODEX_LB_DEBUG_ENDPOINTS_ENABLED=1` and restart `codex-lb`
- `GET /debug/lb/events?limit=50` to see the most recent selections (including `selected.email`)
- `GET /debug/lb/state` to see the current snapshot, eligibility, and cooldown/backoff state
- When disabled (default), these endpoints return 404.
