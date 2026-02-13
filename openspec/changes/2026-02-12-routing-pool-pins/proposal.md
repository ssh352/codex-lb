# Proposal: Add a routing pool (multi-pin) for proxy selection

## Problem

Operators sometimes need to force traffic to a subset of accounts (e.g. debug, controlled rollout, isolate issues)
without pausing/resuming every other account.

## Goals

- Allow selecting one or more accounts as a routing pool.
- When the pool is configured, route within it; if it is unusable, fall back to normal waste-pressure routing.
- Surface pinned state in dashboard/account APIs for UI display.
- Support Gmail-style multi-select in the accounts table for bulk actions.

## Non-goals

- Changing the load balancing scoring algorithm beyond applying a pool filter.
- Replacing the dashboard frontend stack (keep Alpine.js + plain HTML).

