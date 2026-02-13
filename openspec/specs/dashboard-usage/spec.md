# Dashboard Usage: Quota Reset & Pace

## Requirements

- Dashboard APIs MUST expose quota reset timestamps as ISO 8601 strings (`datetime` in schemas) or `null` when unavailable.
- For the **secondary (7d) usage window**, the summary window `reset_at` MUST be the earliest (`min`) reset timestamp among accounts with a known reset time.
- A per-account `reset_at_secondary` value MUST reflect that account’s own secondary (7d) reset timestamp (or `null` when unavailable).
- “Quota pace (7D)” calculations MUST use the secondary (7d) summary window’s `remaining_credits` and `reset_at` values.

