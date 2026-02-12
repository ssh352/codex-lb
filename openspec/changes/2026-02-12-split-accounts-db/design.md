# Design

## Configuration

- Add `CODEX_LB_ACCOUNTS_DATABASE_URL` (required, defaults to `~/.codex-lb/accounts.db`).
  - It must be different from `CODEX_LB_DATABASE_URL`.
  - Accounts are stored in the accounts database and usage/log tables remain in the main database.

## Storage boundaries

- Accounts DB: `accounts` table.
- Main DB: `usage_history`, `request_logs`, `sticky_sessions`, `dashboard_settings`.

## Foreign keys

SQLite cannot enforce foreign keys across database files. The main DB tables store `account_id` as
an un-constrained string when split mode is enabled.

## Migration strategy

- New installs: create tables in each DB according to their scope.
- Existing installs:
  - Apply a SQLite migration that rebuilds `usage_history`, `request_logs`, and `sticky_sessions` to
    remove foreign keys referencing `accounts`.
  - Provide a one-shot CLI command (`codex-lb migrate-accounts`) to copy legacy `accounts` rows from
    `store.db` into `accounts.db` (optional `--drop-legacy`).

## SQLite journaling (ops)

- Main DB (`store.db`) uses WAL journaling for write-heavy operational tables.
- Accounts DB (`accounts.db`) uses rollback journaling (no WAL/SHM sidecar files) to reduce file-sync
  desynchronization hazards when the goal is roaming accounts across machines via iCloud/Dropbox.
