# Proposal: Fix new account routing priority when `prefer_earlier_reset=true`

## Problem

When `prefer_earlier_reset_accounts=True` is enabled, brand new accounts with no usage history (and
therefore no `secondary_reset_at`) are assigned `UNKNOWN_RESET_BUCKET_DAYS` (10,000) and sorted last.
This causes new accounts to be effectively never selected while any existing account is available.

## Goals

- New accounts with no `secondary_reset_at` are treated as highest priority when
  `prefer_earlier_reset_accounts=True`.
- Preserve current behavior when `prefer_earlier_reset_accounts=False`.
- Add unit tests to lock in the intended selection order.

## Non-goals

- Changes to sticky session behavior.
- Changes to usage-based selection logic beyond the reset bucket handling.

