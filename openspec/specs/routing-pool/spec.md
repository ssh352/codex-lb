# Routing Pool (Pinned Accounts)

## Requirements

- The system MUST store a routing pool as a list of account IDs (`pinned_account_ids`) in the main operational database dashboard settings.
- When `pinned_account_ids` is non-empty, the proxy account selection MUST restrict eligible accounts to the accounts listed in `pinned_account_ids`.
- If no eligible accounts are available within `pinned_account_ids` (e.g. all are paused, deactivated, rate limited, or quota exceeded), the proxy account selection MUST fall back to normal selection over all accounts.
- Selection MUST treat accounts with `secondary_used_percent >= 100` as ineligible for routing until their `secondary_reset_at` boundary has passed (when `secondary_reset_at` is known).
- Selection MUST rank eligible accounts by maximizing `tier_weight / time_to_secondary_reset_seconds`, where:
  - `tier_weight` defaults to `pro=1.00`, `plus=0.72`, and `free=0.512`.
  - `time_to_secondary_reset_seconds` is `max(60, secondary_reset_at - now)` when `secondary_reset_at` is known.
  - when `secondary_reset_at` is unknown, the account's selection score MUST be treated as `0`.
- Selection MUST use deterministic tie-breaks when scores are equal:
  1. earlier `secondary_reset_at` (known sorts before unknown)
  2. higher `tier_weight`
  3. lexical `account_id`
- If all eligible accounts have unknown `secondary_reset_at` (all selection scores are `0`), selection MUST fall back to deterministic ordering by:
  1. higher `tier_weight`
  2. lexical `account_id`
- When an account becomes `quota_exceeded`, the system MUST remove that account ID from `pinned_account_ids`.
- The dashboard settings API MUST expose `pinned_account_ids` as a list of strings.
- Account summary payloads returned by dashboard/account listing APIs MUST include a boolean `pinned` field that is `true` when the account ID is present in `pinned_account_ids`.
