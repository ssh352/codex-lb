# Proposal: Persist Codex session identifiers in request logs

## Problem

codex-lb persists per-request routing decisions (`request_id` + `account_id`) in `request_logs`, but
the Codex CLI "session" identifiers (for example `x-codex-session-id`) are not stored. This makes it
hard to answer: "for a given Codex session, which upstream account was used for each request?"

## Goals

- Persist `x-codex-session-id` and `x-codex-conversation-id` on each `request_logs` row.
- Keep the feature opt-in only via client headers, with a safe fallback: when no header is provided
  and the request `prompt_cache_key` is a UUID, store that UUID as `codex_session_id`.
- Make the identifiers usable for local debugging/analytics (e.g. search + SQL queries).

## Non-goals

- Multi-user tenancy / access control; this is for single-user deployments.
- Encrypting identifiers at rest.
- Defining a stable "session" concept beyond the client-provided headers.
