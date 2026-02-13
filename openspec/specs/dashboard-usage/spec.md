# Dashboard Usage: Quota Reset

## Requirements

- Dashboard APIs MUST expose quota reset timestamps as ISO 8601 strings (`datetime` in schemas) or `null` when unavailable.
- For the **secondary (7d) usage window**, the summary window `reset_at` MUST be the earliest (`min`) reset timestamp among accounts with a known reset time.
- A per-account `reset_at_secondary` value MUST reflect that account’s own secondary (7d) reset timestamp (or `null` when unavailable).
