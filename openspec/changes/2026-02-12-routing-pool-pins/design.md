# Design

## Storage

- Store the routing pool in `dashboard_settings` as `pinned_account_ids_json` (JSON array of strings).
- Expose the pool via the dashboard settings API as `pinnedAccountIds` (camelCase).

## Routing behavior

- Load balancer snapshot includes `pinned_account_ids`.
- When the pool is non-empty, selection is attempted within the pool first.
- If selection within the pool yields no account, retry selection using the full account set (waste-pressure fallback).

## Dashboard/UI

- Account summaries include `pinned: bool` derived from current routing pool membership.
- Accounts table supports multi-select (shift-range) and bulk actions (pause/resume/delete/add/remove from pool).

