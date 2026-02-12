## Proxy snapshot TTL (“freshness”) and why `10` is often better than `1`

`CODEX_LB_PROXY_SNAPSHOT_TTL_SECONDS` controls how long the proxy load balancer reuses a cached
selection snapshot before rebuilding it from the DB.

### What’s in the snapshot

- Accounts list + statuses
- Latest usage entries (primary/secondary) used for selection
- Runtime state derived from request outcomes (cooldowns, last error, etc.)

### What “freshness” means

With a TTL of `N` seconds, DB-driven changes can take up to ~`N` seconds to influence selection:

- Usage refresh writes new usage rows → selection may keep using the prior usage snapshot until TTL
  expiry.
- Dashboard edits (enable/disable accounts, preference flags) → selection may keep using the prior
  values until TTL expiry.

This is usually acceptable because usage refresh defaults to a 60s interval, and most operators
don’t require sub-second reaction to dashboard toggles.

### Why `10` often improves latency under concurrency

Under bursty concurrency, rebuilding the snapshot too frequently (e.g. TTL `1`) can cause many
requests to hit the DB for the same “accounts + latest usage” queries. On SQLite, this can increase
lock contention and inflate p50/p95.

Raising the TTL (e.g. `5–10`) reduces redundant DB work and generally improves responsiveness.

### Important nuance: request-observed failures invalidate the snapshot immediately

The proxy invalidates the snapshot on key error events (rate limit/quota/permanent failure). This
means the system can still react quickly to an account becoming “bad” due to errors seen on live
requests, even with a higher TTL.
