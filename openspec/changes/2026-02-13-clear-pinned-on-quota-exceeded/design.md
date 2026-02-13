# Design

## Behavior

- When an account becomes `quota_exceeded`, the system removes that account ID from the routing pool
  (`pinned_account_ids`).

## Implementation notes

- Add a `SettingsRepository` helper to remove one or more pinned account IDs.
- Apply pruning in two places:
  - `LoadBalancer.mark_quota_exceeded` (immediate response to upstream quota errors).
  - `LoadBalancer._get_snapshot` after usage-based status sync (covers quota exceeded derived from usage windows).

