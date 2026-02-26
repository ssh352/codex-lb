# Notes (evidence and queries)

## Snapshot (2026-02-26)

All queries below use `~/.codex-lb/store.db` and cover the last 48 hours relative to the query runtime.

### Error distribution

```sql
SELECT COALESCE(error_code,'(null)') AS code, COUNT(*)
FROM request_logs
WHERE status='error'
  AND requested_at >= datetime('now', '-48 hours')
GROUP BY code
ORDER BY COUNT(*) DESC;
```

### `usage_limit_reached` volume

```sql
SELECT COUNT(*)
FROM request_logs
WHERE status='error'
  AND error_code='usage_limit_reached'
  AND requested_at >= datetime('now', '-48 hours');
```

### Next-request timing after `usage_limit_reached` ends

```sql
WITH e AS (
  SELECT
    id,
    account_id,
    requested_at,
    latency_ms,
    datetime(requested_at, printf('+%f seconds', COALESCE(latency_ms,0)/1000.0)) AS ended_at
  FROM request_logs
  WHERE status='error'
    AND error_code='usage_limit_reached'
    AND requested_at >= datetime('now', '-48 hours')
),
next_req AS (
  SELECT
    e.id,
    (
      SELECT (julianday(r.requested_at)-julianday(e.ended_at))*86400
      FROM request_logs r
      WHERE r.account_id=e.account_id AND r.requested_at > e.ended_at
      ORDER BY r.requested_at ASC
      LIMIT 1
    ) AS next_delta_s
  FROM e
)
SELECT
  COUNT(*) AS n_events,
  SUM(CASE WHEN next_delta_s < 10 THEN 1 ELSE 0 END) AS next_lt_10s,
  SUM(CASE WHEN next_delta_s < 60 THEN 1 ELSE 0 END) AS next_lt_60s,
  SUM(CASE WHEN next_delta_s < 300 THEN 1 ELSE 0 END) AS next_lt_5m
FROM next_req;
```

### Secondary usage at/before the `usage_limit_reached` event time

```sql
WITH ulr AS (
  SELECT id, account_id, requested_at
  FROM request_logs
  WHERE status='error'
    AND error_code='usage_limit_reached'
    AND requested_at >= datetime('now', '-48 hours')
)
SELECT
  COUNT(*) AS n_events,
  SUM(CASE WHEN (
    SELECT uh.used_percent
    FROM usage_history uh
    WHERE uh.account_id=ulr.account_id
      AND uh.window='secondary'
      AND uh.recorded_at <= ulr.requested_at
    ORDER BY uh.recorded_at DESC
    LIMIT 1
  ) >= 100.0 THEN 1 ELSE 0 END) AS sec_ge_100_at_event,
  SUM(CASE WHEN (
    SELECT uh.used_percent
    FROM usage_history uh
    WHERE uh.account_id=ulr.account_id
      AND uh.window='secondary'
      AND uh.recorded_at <= ulr.requested_at
    ORDER BY uh.recorded_at DESC
    LIMIT 1
  ) < 100.0 THEN 1 ELSE 0 END) AS sec_lt_100_at_event
FROM ulr;
```

