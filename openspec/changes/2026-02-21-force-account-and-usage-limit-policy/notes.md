# Notes

## Bug: dashboard shows “Blocked · Try again now” indefinitely

### Summary

An account can remain persisted as `rate_limited` / `quota_exceeded` in `accounts.db` even after its stored
`accounts.reset_at` timestamp is in the past. When that happens, the dashboard continues to render a blocked label
(`Blocked · Try again now`) because the account `status` is still “limited/exceeded”, while the computed retry boundary
(`statusResetAt`) is “now”.

This is confusing to operators because it looks like the system is still blocking the account even though the retry
time has already passed.

### Symptoms (what you see)

- Accounts table:
  - Status pill shows **Rate limited** (or **Quota exceeded**).
  - Sublabel shows **Blocked · Try again now**.
- Selected account panel:
  - **Blocked until** shows **now**.
- Routing may still succeed (because in-memory routing can recover), but the persisted status in `accounts.db` remains
  blocked until an explicit action clears it.

### Root cause (current behavior)

- Persistence:
  - `accounts.status` and `accounts.reset_at` are persisted when the load balancer marks an account blocked.
  - There is no periodic/background “status unstick” that writes `accounts.status=active` back to `accounts.db` when
    `reset_at <= now`.
- Derivation:
  - The dashboard computes `statusResetAt` (aka “blocked until”) from persisted `accounts.reset_at` and/or usage window
    reset timestamps.
  - The dashboard UI renders “Blocked · Retry …” when `status in {limited, exceeded}` *and* `statusResetAt` is present.

### Minimal repro

1. Ensure an account row exists with:
   - `status = RATE_LIMITED` (or `QUOTA_EXCEEDED`)
   - `reset_at` set to a timestamp in the near future.
2. Wait until after `reset_at` passes.
3. Refresh the dashboard.

Expected:
- Status automatically returns to **Active** (or at least the UI stops showing it as blocked).

Actual:
- Status remains blocked, and the UI shows **Blocked · Try again now**.

### Workaround (today)

- Use the dashboard action **Resume** (calls `POST /api/accounts/{account_id}/reactivate`) to clear the persisted
  blocked status.

### Where this logic lives (code pointers)

- Blocked metadata exposed to the dashboard: `app/modules/accounts/mappers.py`
- Blocked label rendering (“Blocked · Try again …”): `app/static/index.js` + `app/static/index.html`
- Persisted status/reset writes on mark: `app/modules/proxy/load_balancer.py` (`AccountsRepository.update_status`)
- Auto-clear only happens in-memory during selection: `app/core/balancer/logic.py` (`select_account()` resets state)

### Possible fixes (design directions)

Any fix should make persisted state converge so the dashboard reflects reality without manual “Resume”.

Options include:
- Clear persisted `accounts.status/reset_at` when a blocked account becomes eligible again (e.g., on snapshot refresh,
  selection, or a periodic reconcile task).
- Change the dashboard to derive a separate “effective status” for display when `statusResetAt <= now`.
- Add/extend a CLI/API reconcile command to actively clear stale blocked states (not just set them).

