# Design

## Selection sort key

The bug is in the reset-first sort key used when `prefer_earlier_reset_accounts=True`.

Current behavior:
- `secondary_reset_at is None` is treated as "unknown", assigned to a very large bucket (10,000),
  which deprioritizes new accounts.

Desired behavior:
- `secondary_reset_at is None` should be treated as bucket `0` (highest priority), so new accounts
  can be selected and start accumulating usage history.
- For accounts with a reset time, use `max(0, days_until_reset)` as the bucket.

## Tests

Update the existing unit test (if present) that asserts missing `secondary_reset_at` is
deprioritized, and add coverage for:

- New account with no reset date wins when `prefer_earlier_reset=true`.
- Multiple new accounts are still ordered by usage (tie-breaker behavior remains stable).
- Behavior is unchanged when `prefer_earlier_reset=false`.

## Risk

Low. The change is localized to the sort key; the remaining selection and tie-break logic is
unchanged.

