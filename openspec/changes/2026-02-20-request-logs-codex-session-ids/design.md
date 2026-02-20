# Design

## Storage

- Main DB (`store.db`) `request_logs` gains two nullable columns:
  - `codex_session_id` (raw `x-codex-session-id`)
  - `codex_conversation_id` (raw `x-codex-conversation-id`)

These are stored *raw* (not hashed) to keep local, personal deployments easy to query without an
extra hashing step.

If this project ever evolves into a shared deployment, switch to storing an HMAC hash instead and
avoid emitting the raw values in logs.

## Write path

- Extract the two header values from inbound proxy requests.
- Attach them to request-log persistence in both code paths:
  - buffered inserts (`RequestLogCreate` + flush scheduler)
  - direct DB writes (`RequestLogsRepository.add_log`)

## Querying

- Extend request-log search to include the new columns so dashboards can find requests by session id.

