# Routing Pool (Pinned Accounts)

## Requirements

- The system MUST store a routing pool as a list of account IDs (`pinned_account_ids`) in the main operational database dashboard settings.
- When `pinned_account_ids` is non-empty, the proxy account selection MUST restrict eligible accounts to the accounts listed in `pinned_account_ids`.
- If no eligible accounts are available within `pinned_account_ids` (e.g. all are paused, deactivated, rate limited, or quota exceeded), the proxy account selection MUST fall back to normal waste-pressure selection over all accounts.
- When an account becomes `quota_exceeded`, the system MUST remove that account ID from `pinned_account_ids`.
- The dashboard settings API MUST expose `pinned_account_ids` as a list of strings.
- Account summary payloads returned by dashboard/account listing APIs MUST include a boolean `pinned` field that is `true` when the account ID is present in `pinned_account_ids`.
