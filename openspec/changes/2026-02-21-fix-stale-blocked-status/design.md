# Design

## User-visible impact

After this change, when an account’s effective “blocked until” time is in the past, the dashboard will show the account
as **Active** (and stop rendering “Blocked · Try again now”) without requiring a manual **Resume** action.

## Root cause (current behavior)

- The dashboard “blocked until” UX is driven by:
  - persisted `accounts.status` (`rate_limited` / `quota_exceeded`) and
  - a computed “blocked until” timestamp (`statusResetAt`) derived from `accounts.reset_at` and usage window reset
    timestamps.
- The load balancer auto-clears blocked states in-memory during routing, but does not guarantee a DB write-back in the
  absence of traffic.
- Therefore, a stale blocked status can remain in `accounts.db` indefinitely.

## Current code status (already implemented in one place)

The dashboard already performs stale blocked-status clearing in both:

- `GET /api/dashboard/overview` (`app/modules/dashboard/service.py`), and
- `GET /api/accounts` (`app/modules/accounts/service.py`).

Both use the shared helper `app/modules/accounts/status_reconcile.py:stale_blocked_account_ids`.

If the “Blocked · Try again now” symptom still appears in the UI, it is likely due to runtime/deployment state (e.g.
server not restarted after upgrading code, or the running server is pointed at a different `accounts.db` than the one
being inspected).

## Fix strategy

Extend the existing reconciliation behavior to `GET /api/accounts` (AccountsService list) so both dashboard views
converge persisted blocked status.

### Effective blocked boundary

For each account with a persisted blocked status:

- If status is `rate_limited`:
  - `effective_reset = max(accounts.reset_at, primary_usage.reset_at)` considering only non-null values.
- If status is `quota_exceeded`:
  - `effective_reset = max(accounts.reset_at, secondary_usage.reset_at)` considering only non-null values.
- If `effective_reset` is `null`, do nothing (cannot safely determine eligibility).

### Reconciliation rule

If `effective_reset` is not null and `effective_reset <= now_epoch`:

- persistently clear the blocked state by setting:
  - `accounts.status = active`
  - `accounts.reset_at = null`
  - `accounts.deactivation_reason` unchanged (or null if the repository API requires it)

### Response correctness

The `GET /api/accounts` response MUST reflect the updated state (no “Active”/“Blocked” mismatch). Acceptable approaches:

- Update the in-memory ORM objects to match the DB update before mapping to response schemas, or
- Re-load the accounts after the bulk update (extra DB read but simplest).

### Cache behavior

The accounts list cache MUST be invalidated when reconciliation performs updates, otherwise the dashboard can keep
serving stale status.

## Non-goals

- Changing proxy routing/selection behavior.
- Introducing new configuration keys or multiple env-var fallbacks.
- Automatically clearing blocked states when `effective_reset` is unknown.
