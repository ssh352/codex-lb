# Design

## Operator UX goal

Operators should be able to answer “when should routing try this account again?” without a redundant-looking
“Blocked until” field that often matches the 7-day quota reset.

## Selected account panel changes

- Remove the dedicated “Blocked until” field.
- When the selected account is blocked and `statusResetAt` is present:
  - If status is `limited` (rate limited):
    - The primary reset row label becomes “Retry at (5h)”.
    - The value is derived from `statusResetAt` (effective retry boundary).
    - Tooltip explains this is the effective retry boundary for routing.
  - If status is `exceeded` (quota exceeded):
    - The secondary reset row label becomes “Retry at (7d)”.
    - The value is derived from `statusResetAt` (effective retry boundary).
    - Tooltip explains this is the effective retry boundary for routing.
- When the selected account is not blocked (or `statusResetAt` is null), display the raw window reset timestamps
  as before.

## Contracts

- `statusResetAt` remains the SSOT for “effective retry boundary” in dashboard APIs and continues to be exposed as an
  ISO 8601 datetime or null.
