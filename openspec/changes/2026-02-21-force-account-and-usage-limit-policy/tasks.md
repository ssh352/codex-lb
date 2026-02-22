# Tasks

- [x] Do not forward `x-codex-lb-force-account-id` to upstream.
- [x] Add forced-account selection path and disable failover when forced.
- [x] Add integration test ensuring forced-account routing wins over pinned pool.
- [x] Cap initial `usage_limit_reached` cooldown and escalate only on repeated errors or weekly exhaustion.
- [x] Update unit tests for `handle_usage_limit_reached` to reflect the new policy.
- [x] Expose `statusResetAt` in `/api/accounts` so the dashboard can show when a limited/exceeded account becomes selectable.
- [x] Update dashboard UI to display "Blocked · Retry …" and "Blocked until …" when `statusResetAt` is present.
