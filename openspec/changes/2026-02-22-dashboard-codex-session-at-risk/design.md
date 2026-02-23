# Design

## Background

`request_logs` already includes two nullable columns:

- `codex_session_id` (raw `x-codex-session-id`, with a UUID fallback from `prompt_cache_key`)
- `codex_conversation_id` (raw `x-codex-conversation-id`)

The purpose of this change is to **surface** that data in the dashboard and add a report that correlates session IDs
with “low weekly remaining” accounts.

This is an automation convenience. The same correlation can be answered today with one-off SQL (see `notes.md`).

## API changes

### 1) Add session identifiers to request log entries

Extend the dashboard request logs API response model (`RequestLogEntry`) with:

- `codex_session_id: str | None`
- `codex_conversation_id: str | None`

These fields are additive and should be serialized as camelCase:

- `codexSessionId`
- `codexConversationId`

### 2) Add “at‑risk Codex sessions” endpoint

Add a new endpoint under the request logs API:

`GET /api/request-logs/codex-sessions/at-risk`

Query params:

- `limit` (default: 50, max: 500)
- `sinceHours` (default: 168; last 7 days)
- `thresholdRemainingPercent` (default: 10; “at-risk if secondary remaining <= threshold”)

Response shape (typed Pydantic models, additive API):

- `sessions[]` where each entry contains:
  - `codexSessionId: string`
  - `lastSeen: datetime` (ISO 8601 string via `DashboardModel`)
  - `requestCount: int`
  - `accounts[]` where each entry contains:
    - `accountId: string`
    - `email: string | null`
    - `secondaryUsedPercent: float | null`
    - `secondaryRemainingPercent: float | null`
    - `secondaryResetAt: datetime | null` (converted from epoch)

## Data flow / computation

### Determine “at-risk” accounts

Use existing DB telemetry (`usage_history`) and its effective secondary-window logic:

- Use “latest secondary usage per account” (same semantics as `UsageRepository.latest_by_account(window="secondary")`).
- Compute `secondary_remaining_percent = max(0, 100 - used_percent)`.
- Mark the account as at-risk when `secondary_remaining_percent <= thresholdRemainingPercent`.

### Determine “at-risk” Codex sessions

From `request_logs`:

- Filter to rows where:
  - `codex_session_id IS NOT NULL AND codex_session_id != ''`
  - `requested_at >= (now - sinceHours)`
  - `account_id IN at_risk_account_ids`
- Group by `codex_session_id` and compute:
  - `last_seen = max(requested_at)`
  - `request_count = count(*)`
- Order by `last_seen DESC` and apply `limit`.

Account enrichment:

- For each selected `codex_session_id`, collect distinct `(codex_session_id, account_id)` pairs and map:
  - `account_id -> email` via `accounts.db`
  - `account_id -> latest secondary usage` via the usage snapshot computed above

Avoid relying on `group_concat()` to keep the response assembly typed and robust for large sessions.

## Dashboard UI/UX changes

### 1) Recent requests table

- Add a “Session” column showing a short prefix of `codexSessionId` (e.g., first 8 chars).
- Tooltip shows full session id.
- Add a “Copy” button using existing clipboard helper.

### 2) At‑risk sessions panel

Add a small panel listing:

- Session id (short + copy)
- Last seen
- Accounts (comma-separated emails; fall back to account_id when email missing)
- Request count

The panel should auto-refresh with the rest of the dashboard and should not require the operator to know any session IDs.

## Edge cases

- A single Codex session may touch multiple accounts (failover/retries). The report must display multiple accounts per
  session.
- Accounts may be missing from `accounts.db` (email unknown). Preserve `accountId` and set `email=null`.
- Usage snapshots may be missing for an account; usage fields should be nullable in the report.
