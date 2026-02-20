# Proposal: Load Balancer Debug Introspection (State + Recent Events)

## Problem

When debugging “why accounts are not used” (especially with routing pool pins + stickiness), the current observability
surface is insufficient:

- `~/.codex-lb/log` does not record per-request account selection decisions or per-account ineligibility reasons.
- `/metrics` provides useful aggregates (selection outcomes + mark events) but does not provide per-account runtime state
  (cooldowns/backoff) or a per-request selection trail.
- The load balancer’s key decision inputs (cooldowns, error backoff, sticky mappings) are in-memory and not persisted.

As a result, operators cannot reliably answer questions like:

- “Why is account X ineligible right now?”
- “Are we falling back from pinned pool → full pool?”
- “Which account did we select for request_id Y?”

## Goals (layered observability)

This change adopts a layered approach:

1) **Always-on, low-noise logs** for high-signal events (no per-request selection logs):
   - when pinned-pool selection fails and the balancer falls back to full-pool selection
   - when the balancer applies a mark that changes eligibility (rate-limit-like cooldown/backoff)
2) **On-demand debug endpoints** (gated) that expose:
   - current per-account eligibility and ineligibility reasons (pinned pool and full pool)
   - the effective pinned pool and sticky distribution summary
   - latest primary/secondary usage snapshots used by selection
3) **Recent selection history** via a bounded in-memory ring buffer (queried via debug endpoint).

Keep default behavior unchanged: debug endpoints are **disabled by default** and not exposed in OpenAPI.

## Non-goals

- No persistent selection-history database writes on the proxy hot path.
- No exposure of raw sticky keys or prompt-derived identifiers.
- No high-volume “log every selection attempt” mode.
- No authentication/authorization system for debug endpoints (intended for localhost-only usage when explicitly enabled).

## Acceptance criteria

- When `CODEX_LB_DEBUG_ENDPOINTS_ENABLED=false` (default), debug endpoints return 404.
- When `CODEX_LB_DEBUG_ENDPOINTS_ENABLED=true`:
  - `GET /debug/lb/state` returns a JSON payload describing current eligibility and state for all accounts in the snapshot.
  - `GET /debug/lb/events?limit=N` returns the most recent selection events (newest first), bounded by `N`.
  - Per-account fields include both persisted state (status/reset) and runtime state (cooldown/backoff) where available.
- The proxy emits targeted log lines for:
  - pinned-pool selection failure + full-pool fallback
  - mark events that apply cooldown/backoff
