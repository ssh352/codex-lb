# Notes: Questions / tradeoffs for stream buffering

## Q: How does Mode B (“prelude buffering”) feel?
- Mode B adds a short delay before the stream starts (typically under a second), then behaves like normal streaming.
- Mode B can hide failures that happen after `response.created` but before token deltas.
- Mode B cannot hide failures that happen after token deltas have started; in those cases, interruptions remain possible.

## Q: From real data, how much can Mode B help?
From `~/.codex-lb/store.db` (24h window as of 2026-02-23):

- `usage_limit_reached` request_ids: 104 total
  - 39 retried and succeeded
  - 52 were single-attempt only (no retry)
  - 13 retried but had no success

For the 52 single-attempt-only `usage_limit_reached` rows, the error request latency distribution was:
- 0 under 0.75s (so a 750ms prelude timeout likely helps ~0%)
- 14 under 2s (upper bound: ~27% of single-attempt cases could be retried invisibly)
- 27 under 3s (upper bound: ~52%)
- 47 under 5s (upper bound: ~90%)

If the “single attempt” cases are mostly caused by `response.created` being emitted before the failure (the expected
cause), then increasing `PRELUDE_TIMEOUT_MS` to ~3–5s (or using a delta-gated prelude that flushes only on the first
delta/terminal event) should convert many of those 52 cases into real retries. Historically, when retries do happen in
this window, ~75% of them succeed (39/(39+13)), so a rough expectation is:

- With a 5s prelude: up to ~47 additional retries; ~35 additional successes (75%); net reduction in “stops due to
  `usage_limit_reached`” on the order of ~30–40% (not 100%).

## Q: Is option 2 (non-streaming compact) useful? Why not choose “both”?
It can be useful, but it’s a different product shape.

- “Compact” (single JSON response) is naturally interruption-free from the client’s perspective, but it requires the
  client to use a non-streaming endpoint/flow. Codex’s default UX is streaming-oriented.
- The plan’s buffering modes work on the existing streaming endpoint (`/responses` SSE) without requiring client changes.

Why not “both” by default:
- Supporting both is fine, but it doesn’t solve the main issue unless the client actually switches to compact.
- Implementing buffering provides the “no interruption” guarantee while keeping the same streaming interface.

Practical recommendation:
- Implement buffering modes first (proxy-only, no client changes).
- If you later control the client, consider a “non-streaming / compact” mode as an additional UX option for long-running
  tasks where streaming isn’t required.

## Q: How does this relate to the earlier “Upgrade to Plus” message?
Local request logs show the dominant failure is already `error_code=usage_limit_reached` (structured error). The literal
“Upgrade to Plus …” message wasn’t found in recent DB logs, so message/HTML classification is secondary hardening, not the
main fix for your observed interruptions.
