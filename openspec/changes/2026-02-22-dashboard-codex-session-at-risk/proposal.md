# Proposal: Surface Codex session → upstream account mapping (and “at‑risk sessions”) in the dashboard

## Problem

Codex CLI sessions sometimes emit a warning like “less than 10% of your weekly limit left”, but when routing through
codex-lb it is not obvious **which upstream ChatGPT login** (email/account) actually served those requests.

codex-lb already persists `codex_session_id` / `codex_conversation_id` on `request_logs` (see change
`2026-02-20-request-logs-codex-session-ids`), but today:

- The dashboard request table does not show the session identifiers, making it hard to correlate “that Codex session”
  with upstream account selection.
- There is no automated report answering: “Which recent Codex sessions have been using accounts that are ≤10% weekly
  remaining (secondary window)?”

This forces operators to run ad-hoc SQL against local SQLite.

If you want the answer immediately without code changes, run the one-off `sqlite3` queries in `notes.md`.

## Goals

- Expose `codex_session_id` / `codex_conversation_id` in the request logs API and dashboard UI.
- Add an API + dashboard report that lists recent Codex sessions that touched “at‑risk” accounts (default: secondary
  remaining ≤ 10%), including the upstream emails used.
- Keep the solution local-first: compute from `store.db` + `accounts.db` only (no network calls).

## Non-goals

- Changing Codex CLI behavior or its warning text.
- Changing routing/failover policy (this is observability only).
- Defining any new “session” concept beyond the client-provided identifiers.
- Adding new persistence of identifiers beyond what is already stored in `request_logs`.
