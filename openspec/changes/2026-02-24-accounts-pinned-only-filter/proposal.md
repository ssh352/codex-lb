# Proposal: Add “Pinned only” filter to Accounts page

## Problem

Pinned accounts can be scattered throughout the Accounts table due to sorting and large account counts. This makes it tedious to review pinned membership and to unpin accounts quickly.

## Goals

- Provide an explicit “Pinned only” mode that filters the Accounts list to pinned accounts.
- Keep the control lightweight and discoverable (toggle near the Accounts search input).
- Ensure selection + bulk actions operate on the currently filtered set.

## Non-goals

- Changing routing / pinned-pool semantics (this is UI-only).
- Reworking table sorting or adding a dedicated pinned-at-top section.

