# Tasks

- [x] Expose `codexSessionId` in `RequestLogEntry` (API) and request log mapping.
- [x] Expose `codexConversationId` in `RequestLogEntry` (API) and request log mapping.
- [x] Update request logs integration tests to assert the new fields when present.
- [ ] Add repository/service logic for `/api/request-logs/codex-sessions/at-risk`.
- [ ] Add integration tests for the at-risk sessions endpoint (usage history + request logs + email mapping).
- [x] Update dashboard “Recent requests” table to display + copy `codexSessionId`.
- [x] Update dashboard CSS for the new “Session” column (alignment/width).
- [ ] Add dashboard panel for “At-risk Codex sessions” backed by the new endpoint.
