# Proposal: Split accounts storage into a separate SQLite database

## Problem

`store.db` contains both low-churn account credentials (encrypted) and high-churn operational data
(usage history + request logs). Users who want to sync/roam accounts (e.g. via iCloud Drive) do not
want to sync the append-heavy tables.

## Goals

- Allow storing accounts (including encrypted OAuth tokens) in a separate SQLite file.
- Keep usage history and request logs in the main database file by default.
- Preserve backwards compatibility when the split setting is not configured.

## Non-goals

- Cross-machine multi-writer support for the same SQLite file.
- Enforcing cross-database foreign keys (unsupported by SQLite).

