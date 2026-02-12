## Proxy + Usage Performance (2026-02-12)

This change targets lower tail latency and better behavior under concurrency for:

- Proxy endpoints (`/backend-api/codex/*`, `/v1/*`)
- Background usage refresh

### High impact, easy fixes (start here)

1) **Run without reload in production**
   - `--reload` adds overhead and can distort perf profiles.

2) **Avoid SQLite writes on the proxy hot path (single-instance mode)**
   - Prefer in-process stickiness:
     - `CODEX_LB_STICKY_SESSIONS_BACKEND=memory`
   - Prefer buffered request logging:
     - `CODEX_LB_REQUEST_LOGS_BUFFER_ENABLED=true`

3) **Disable noisy logging**
   - Prefer:
     - `CODEX_LB_ACCESS_LOG_ENABLED=false`
   - Leave request/shape/payload logging disabled unless actively debugging:
     - `CODEX_LB_LOG_PROXY_REQUEST_SHAPE=false`
     - `CODEX_LB_LOG_PROXY_REQUEST_PAYLOAD=false`

4) **Tune proxy selection snapshot TTL for bursty concurrency**
   - `CODEX_LB_PROXY_SNAPSHOT_TTL_SECONDS` trades DB reads vs selection freshness.
   - Start at the default (1s). If DB read pressure is high under bursts, increase (e.g. 5–10s).
   - Local stub benchmark note (2026-02-12): moving from `1` → `10` seconds reduced compact p95 at
     concurrency=50 from ~800ms to ~400ms on this machine.

5) **Set sensible HTTP client limits**
   - `CODEX_LB_HTTP_CLIENT_CONNECTOR_LIMIT` caps concurrent upstream connections.
   - Increase if you see connection pool queuing (and verify OS limits).

### Concurrency guidance (important)

- **SQLite is single-writer**: adding more worker processes can increase write contention and worsen
  tail latency for write-heavy workloads (request logs, sticky sessions, usage history).
- If you must run multiple workers/processes and require stickiness shared across them:
  - Use `CODEX_LB_STICKY_SESSIONS_BACKEND=db` (accepting added DB write load).
  - Re-evaluate whether SQLite still meets your concurrency targets.

### Benchmark / regression plan (repeatable)

Goal: a local harness that distinguishes upstream latency from local contention and catches
regressions in p95/p99 and error rates.

1) **Isolate upstream variability**
   - Run the upstream stub:
     - `/Users/zhang/venv/bin/python scripts/upstream_stub.py --port 9999`
   - Point the server at it:
     - `CODEX_LB_UPSTREAM_BASE_URL=http://127.0.0.1:9999/backend-api`

2) **Compact endpoint baseline**
   - Run:
     - `/Users/zhang/venv/bin/python scripts/perf_compact.py --base-url http://127.0.0.1:2455 --requests 200 --concurrency 20`
   - Record p50/p95/p99 + error rate.
   - Repeat at concurrency = 50 and 100 (or until errors appear).

3) **Streaming endpoint baseline**
   - Run:
     - `/Users/zhang/venv/bin/python scripts/perf_streaming.py --base-url http://127.0.0.1:2455 --requests 50 --concurrency 10 --stub`
   - Record p50/p95/p99 for:
     - `ttfb_ms` (time to first upstream bytes)
     - `duration_ms` (time to full stream completion)
   - Repeat at concurrency = 25 and 50 (or until errors appear).

4) **Define success criteria**
   - Targets per endpoint:
     - p95/p99 thresholds
     - max error rate at specific concurrency
     - CPU / memory ceilings
     - SQLite busy/lock frequency signals

### Diagnostics checklist (when perf is “bad”)

- Compare “real upstream” vs stub runs to decide: upstream-bound vs local-bound.
- If local-bound, check for:
  - sticky backend = `db` (writes on proxy hot path)
  - request logs written per-request instead of buffered
  - too-small snapshot TTL causing bursty DB reads
  - usage refresh interval/concurrency causing write bursts

### Snapshot TTL deep dive

#### Proxy snapshot TTL (“freshness”) and why `10` is often better than `1`

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
