# Tasks

- [x] Add `request_logs.codex_session_id` and `request_logs.codex_conversation_id` columns + migration.
- [x] Plumb inbound Codex headers into request log persistence (buffered + direct).
- [x] When headers are absent, fall back to a UUID `prompt_cache_key` as `codex_session_id`.
- [x] Extend request log search to match session identifiers.
- [x] Add an integration test for searching by session id.
