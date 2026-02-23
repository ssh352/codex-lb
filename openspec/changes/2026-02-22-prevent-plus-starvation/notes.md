# Notes

## Why this change exists

After switching to tier-aware hybrid routing, operators observed Plus accounts with meaningful remaining weekly credits
receiving little/no traffic when a large Free pool exists.

The root issue is that cross-tier tier scoring uses `sum(required_rate)` which scales with tier size. A large Free pool
can dominate selection even when a Plus account is approaching its secondary reset boundary.

## Intent

“Prefer Plus, but consider reset time”:

- Plus should not be starved just because Free has more accounts.
- If a Free account is truly more urgent (closer reset with enough remaining credits), it should still be selected.

This change encodes that intent by switching the tier aggregation from `sum` to `max` (most urgent account in tier),
while keeping mild tier latency weights for close cases.

## Implementation note

There is currently an `IndentationError` in `app/core/config/settings.py` (tab-indented fields around the proxy config
section). This blocks running helper scripts that import `get_settings()`, so the change includes a task to fix it.
