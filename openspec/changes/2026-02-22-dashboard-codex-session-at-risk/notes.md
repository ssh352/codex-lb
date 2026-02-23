# Notes

## Manual SQL (what this change automates)

Find session → accounts used for at-risk accounts (example shape; exact query can vary):

- Attach `accounts.db` and group `request_logs` by `codex_session_id` filtered to accounts whose latest secondary
  `used_percent >= 90`.

This change makes that mapping visible directly in the dashboard UI and via a typed API endpoint, without requiring
operators to run ad-hoc SQL.

### List at-risk accounts (secondary remaining <= 10%)

```bash
sqlite3 "$HOME/.codex-lb/store.db" \
  ".headers on" \
  ".mode column" \
  "ATTACH DATABASE '$HOME/.codex-lb/accounts.db' AS accounts;
   WITH effective AS (
     SELECT
       id,
       account_id,
       recorded_at,
       used_percent,
       reset_at,
       window_minutes,
       CASE
         WHEN COALESCE(window, 'primary') = 'primary' AND window_minutes >= 1440 THEN 'secondary'
         ELSE COALESCE(window, 'primary')
       END AS window_key
     FROM usage_history
   ),
   ranked AS (
     SELECT
       *,
       ROW_NUMBER() OVER (
         PARTITION BY account_id
         ORDER BY recorded_at DESC, id DESC
       ) AS rn
     FROM effective
     WHERE window_key = 'secondary'
   )
   SELECT
     COALESCE(a.email, '') AS email,
     r.account_id,
     round(r.used_percent, 1) AS used_percent,
     round(max(0, 100 - r.used_percent), 1) AS remaining_percent,
     datetime(r.reset_at, 'unixepoch') AS reset_at_utc,
     r.recorded_at AS recorded_at
   FROM ranked r
   LEFT JOIN accounts.accounts a ON a.id = r.account_id
   WHERE rn = 1 AND (100 - r.used_percent) <= 10
   ORDER BY remaining_percent ASC, used_percent DESC
   LIMIT 50;"
```

### List Codex sessions that used at-risk accounts recently (default last 7d)

```bash
sqlite3 "$HOME/.codex-lb/store.db" \
  ".headers on" \
  ".mode column" \
  "ATTACH DATABASE '$HOME/.codex-lb/accounts.db' AS accounts;
   WITH effective AS (
     SELECT
       id,
       account_id,
       recorded_at,
       used_percent,
       reset_at,
       window_minutes,
       CASE
         WHEN COALESCE(window, 'primary') = 'primary' AND window_minutes >= 1440 THEN 'secondary'
         ELSE COALESCE(window, 'primary')
       END AS window_key
     FROM usage_history
   ),
   ranked AS (
     SELECT
       *,
       ROW_NUMBER() OVER (
         PARTITION BY account_id
         ORDER BY recorded_at DESC, id DESC
       ) AS rn
     FROM effective
     WHERE window_key = 'secondary'
   ),
   at_risk_accounts AS (
     SELECT account_id
     FROM ranked
     WHERE rn = 1 AND (100 - used_percent) <= 10
   )
   SELECT
     rl.codex_session_id,
     max(rl.requested_at) AS last_seen,
     count(*) AS requests,
     count(DISTINCT rl.account_id) AS distinct_accounts,
     group_concat(DISTINCT COALESCE(a.email, rl.account_id)) AS accounts
   FROM request_logs rl
   LEFT JOIN accounts.accounts a ON a.id = rl.account_id
   WHERE
     rl.codex_session_id IS NOT NULL AND rl.codex_session_id != ''
     AND rl.requested_at >= datetime('now', '-7 days')
     AND rl.account_id IN (SELECT account_id FROM at_risk_accounts)
   GROUP BY rl.codex_session_id
   ORDER BY last_seen DESC
   LIMIT 50;"
```

### Lookup: for a specific Codex session id, show the upstream email(s) used

Set `SID` to the session id you saw in logs (or from the query above):

```bash
SID="YOUR_CODEX_SESSION_ID"
sqlite3 "$HOME/.codex-lb/store.db" \
  ".headers on" \
  ".mode column" \
  "ATTACH DATABASE '$HOME/.codex-lb/accounts.db' AS accounts;
   SELECT
     rl.requested_at,
     COALESCE(a.email, '') AS email,
     rl.account_id,
     rl.status,
     rl.error_code,
     rl.request_id
   FROM request_logs rl
   LEFT JOIN accounts.accounts a ON a.id = rl.account_id
   WHERE rl.codex_session_id = '$SID'
   ORDER BY rl.requested_at DESC
   LIMIT 100;"
```
