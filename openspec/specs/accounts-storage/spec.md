# Accounts Storage (Split DB)

## Requirements

- The system MUST store account credentials (encrypted OAuth tokens) in a dedicated database separate from the main operational database.
- The system MUST store operational data (usage history, request logs, settings, sticky sessions) in the main operational database.
- The accounts database URL and main database URL MUST be independently configurable and MUST NOT point to the same database.

## SQLite journaling

When the configured databases use **file-backed SQLite** (i.e. not `:memory:`):

- The main operational database connection MUST use WAL journaling mode.
- The accounts database connection MUST use rollback journaling mode (`DELETE`) to avoid WAL/SHM sidecar files.
- The system MUST configure:
  - `busy_timeout` (non-zero)
  - `synchronous=NORMAL`
  - `foreign_keys=ON`

For **in-memory SQLite** (`:memory:`), journaling mode requirements do not apply (SQLite does not support WAL for in-memory databases).
