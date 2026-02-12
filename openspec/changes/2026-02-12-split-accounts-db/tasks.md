# Tasks

- [x] Add `CODEX_LB_ACCOUNTS_DATABASE_URL` setting with path expansion and a default.
- [x] Introduce a separate accounts DB engine/session and dependency providers.
- [x] Route all account repository/service access through the accounts DB session.
- [x] Add a SQLite migration to remove foreign keys from main DB tables referencing `accounts`.
- [x] Provide a one-shot CLI migration (`codex-lb migrate-accounts`) for legacy `store.db` installs.
- [x] Remove cross-DB joins (e.g. request logs email filtering).
- [x] Update tests to exercise the new split DB wiring (at least import + basic CRUD).
