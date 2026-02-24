# Tasks

## Dashboard UI

- [x] Add `accounts.pinnedOnly: boolean` to client state defaults.
- [x] Add a “Pinned only (n)” toggle button next to the Accounts search input.
- [x] Filter the Accounts table rows when `accounts.pinnedOnly` is enabled.
- [x] Keep selection behavior sane when toggling the filter (auto-select a visible account when possible).

## Validation

- [ ] Manual verification:
  - [ ] Toggling “Pinned only” shows only pinned rows.
  - [ ] `Select all` selects only the filtered (pinned) rows.
  - [ ] Bulk `Unpin` works on the filtered selection.
  - [ ] Empty state message reflects pinned-only mode.

