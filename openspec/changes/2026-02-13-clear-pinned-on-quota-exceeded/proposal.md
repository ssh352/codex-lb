# Proposal: Clear routing pool pins on quota exceeded

## Problem

When an account becomes `quota_exceeded`, keeping it in the routing pool (“pinned accounts”) is misleading in the
dashboard and can require manual operator cleanup. It can also reduce the effectiveness of the pool by leaving
unavailable accounts pinned.

## Goals

- Automatically remove accounts from `pinned_account_ids` when they become `quota_exceeded`.
- Ensure the dashboard pinned indicator clears for quota-exceeded accounts without manual intervention.

## Non-goals

- Automatically re-pin accounts after they recover from quota exceeded.
- Changing the proxy selection algorithm beyond pruning pins for quota-exceeded accounts.

