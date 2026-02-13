## Database Growth: `store.db` retention notes

### Current state

`~/.codex-lb/store.db` grows over time because there is no automated retention/cleanup for the main
append-heavy tables.

### Primary growth drivers

- `request_logs`: typically 1 row per proxied request (written from the proxy path).
- `usage_history`: typically 1 row per account per usage refresh interval (default 60s), plus
  occasional additional writes (e.g. on-demand refresh flows).

### Current deletion behavior

Data is removed only via cascade delete when an account is explicitly deleted (which cascades to
usage history, request logs, and sticky sessions for that account). There is no time-based retention
job.

### Impact

- Database file size increases roughly linearly with traffic and uptime.
- Query performance can degrade as tables grow (especially for “latest” or aggregation queries that
  aren’t supported by indexes).
- SQLite won’t generally return disk space to the OS without maintenance steps (e.g. `VACUUM`).

### Practical guidance

- **Size alone isn’t usually the first bottleneck**. For the dashboard and proxy, query shape and
  write contention are typically more important than whether the DB is 50MB vs 500MB.
- **Retention becomes worth doing** when you start hitting disk/backup pain, or when maintenance
  operations (deletes/compaction) become operationally necessary.
- If you do maintenance on a *live* SQLite DB, prefer SQLite-native backups (`.backup`) rather than
  copying the file while it’s being written.

### Options

1) **Retention configuration**
   - Add settings like `CODEX_LB_REQUEST_LOGS_RETENTION_DAYS` and
     `CODEX_LB_USAGE_HISTORY_RETENTION_DAYS`.
   - Default them to “disabled” (or a conservative value) depending on operational expectations.

2) **Scheduled cleanup job**
   - Add a periodic task that deletes old rows and optionally performs maintenance.
   - For SQLite, be careful: deletes + `VACUUM` are write-heavy and can affect tail latency if run
     during peak proxy traffic.

3) **Manual cleanup**
  - Example (SQLite):
     ```sql
     DELETE FROM request_logs
     WHERE requested_at < datetime('now', '-30 days');

     DELETE FROM usage_history
     WHERE recorded_at < datetime('now', '-7 days');

     VACUUM;
     ```

### Notes / caveats

- For SQLite, consider pairing periodic deletes with `PRAGMA wal_checkpoint(TRUNCATE);` if WAL files
  grow substantially.
- If you move to Postgres later, retention can be implemented via partitions / TTL jobs more safely
  under concurrency than SQLite `VACUUM` on a single file.

### Proxy stickiness note (related)

If you run a single `uvicorn` process (the default for `python -m app.cli`), in-process stickiness
is typically best because it avoids SQLite writes on the proxy request hot path.

If you run multiple workers/processes (or multiple machines) and you need stickiness shared across
them or to survive restarts, use DB-backed stickiness instead.

If a routing pool is configured (pinned accounts), stickiness is constrained to that pool; sticky mappings to
unpinned accounts may be dropped and reassigned when the pinned pool is active.
