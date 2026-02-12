# Design

## Proxy stickiness backend

- Add a configurable sticky session backend:
  - `db`: persists stickiness in `sticky_sessions` (safe across restarts/processes; adds write load).
  - `memory`: stores stickiness in-process (fast; not shared across processes; resets on restart).
- Default to `memory` for single-process deployments (e.g. `uv run python -m app.cli`).
- Use `db` when running multiple processes/workers or multiple machines and you need shared stickiness
  and/or stickiness across restarts.

## Request log buffering

- Optionally buffer request logs in memory and flush in batches in a background task to reduce
  per-request commits on SQLite.

## Usage refresh concurrency

- Fetch usage concurrently (network-bound) while keeping DB writes sequential inside a single
  request-scoped `AsyncSession`.
- Keep per-account transactions short (commit per account) to reduce lock hold times on SQLite.
