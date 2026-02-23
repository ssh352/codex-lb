# Tasks

- [ ] Expose `codexSessionId` / `codexConversationId` in `RequestLogEntry` and dashboard request log mapping.
- [ ] Update request logs integration tests to assert the new fields when present.
- [ ] Add repository/service logic for `/api/request-logs/codex-sessions/at-risk`.
- [ ] Add integration tests for the at-risk sessions endpoint (usage history + request logs + email mapping).
- [ ] Update dashboard “Recent requests” table to display + copy `codexSessionId`.
- [ ] Add dashboard panel for “At-risk Codex sessions” backed by the new endpoint.

