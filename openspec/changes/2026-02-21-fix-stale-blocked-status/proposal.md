# Proposal

Fix a dashboard/operator bug where an account can display as “Blocked · Try again now” indefinitely.

Today, `accounts.db` can persist `accounts.status` as `rate_limited` / `quota_exceeded` even after the stored reset
boundary (`accounts.reset_at`) has passed. The proxy/load balancer will clear the blocked state in-memory when routing,
but if no proxy requests occur after the reset moment, the persisted status remains stale. The dashboard reads
`accounts.db`, so it continues to show the account as blocked until an operator manually clicks **Resume**.

Goal: make persisted account state converge automatically so the dashboard reflects actual eligibility without manual
intervention.

