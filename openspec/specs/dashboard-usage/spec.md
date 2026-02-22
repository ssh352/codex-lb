# Dashboard Usage: Quota Reset

## Requirements

- Dashboard APIs MUST expose quota reset timestamps as ISO 8601 strings (`datetime` in schemas) or `null` when unavailable.
- For the **secondary (7d) usage window**, the summary window `reset_at` MUST be the earliest (`min`) reset timestamp among accounts with a known reset time.
- A per-account `reset_at_secondary` value MUST reflect that account’s own secondary (7d) reset timestamp (or `null` when unavailable).

## Requirements (Blocked Status Convergence)

- Dashboard/account listing APIs MUST expose a per-account `statusResetAt` as an ISO 8601 string (`datetime` in schemas) or `null`.
- When an account is persisted as blocked (`rate_limited` / `quota_exceeded`) and its effective `statusResetAt` boundary is known and `<= now`, APIs MUST converge persisted state before responding by clearing:
  - `accounts.status = active`
  - `accounts.reset_at = null`
