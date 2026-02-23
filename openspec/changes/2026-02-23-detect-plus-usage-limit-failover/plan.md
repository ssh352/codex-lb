# Plan: Reduce `usage_limit_reached` stream interruptions (prelude buffering + failover)

## Summary
We want materially fewer visible “stops” when upstream returns `usage_limit_reached` during streaming. In codex-lb today,
failover can only be seamless while the proxy has not yet emitted any SSE lines to the client. In practice, upstream
often emits early events (e.g. `response.created`) before failing, so the client sees an “interruption” even when another
account could succeed.

This plan adds a configurable server-side **prelude buffering** mode for `/responses` streaming that prevents emitting
early non-user-visible SSE events until we either see the first user-visible delta, hit a short timeout/cap, or encounter
a retryable failure (in which case we fail over without the client seeing anything).

Non-goal: guaranteeing “0 interruptions” in all cases. If failures happen after user-visible deltas have already been
emitted, codex-lb cannot seamlessly fail over without risking duplicated or inconsistent output.

## Grounding / observations (from local data)
From `~/.codex-lb/store.db` and `~/.codex-lb/logs/codex-lb.err.log` on 2026-02-23:

- In the last 14 days, request logs contain `error_code=usage_limit_reached` with message “The usage limit has been
  reached”, and do **not** contain the literal “Upgrade to Plus …” text. This suggests the dominant failure shape is a
  structured OpenAI error, not a plain text/HTML fallback.
- In the last 24 hours, 90 distinct `request_id`s had `error_code=usage_limit_reached`:
  - 36 retried and later succeeded (failover worked).
  - 42 were a single attempt only (no failover despite a retryable code).
  - 12 retried but never succeeded (likely no eligible accounts).

The “single attempt only” bucket is consistent with failures happening after the first SSE line was already forwarded.

## Current behavior (SSOT for what must change)
In `ProxyService._stream_with_retry`, retry/failover on retryable errors only happens when no output has been emitted to
the client. Today, “output” means “any SSE line yielded” (not “user-visible tokens”).

Therefore, to eliminate interruptions we must avoid yielding any SSE lines until we know an attempt will succeed (or we
have exhausted retries).

## Proposed behavior (decision complete)
Add a buffering mode for streaming responses:

### Mode B: `prelude` buffering (targets “response.created → failed” without killing streaming)
- Buffer only the initial “prelude” portion of the stream and do not yield it until we see the first user-visible delta
  event (or a short timeout elapses).
- Retryable failures during the prelude trigger failover invisibly (client sees nothing).
- Once the prelude ends without failure, flush buffered events and stream normally (current low-latency behavior).

User-visible UX change (what it feels like):
- Usually a short initial delay (up to `PRELUDE_TIMEOUT_MS`) before the stream “starts”.
- After the prelude flush, streaming behaves like today.
- If the stream fails after prelude flush (i.e. after deltas started), interruptions can still happen; Mode B only aims to
  hide the common early `response.created` → retryable-failure cases.

### Mode C: `off` (current behavior)
- No buffering; lowest latency; interruptions remain possible.

## API / configuration changes
Add settings (env vars) with defaults:

- `CODEX_LB_STREAM_BUFFER_MODE`: `off | prelude` (default: `off`)
- `CODEX_LB_STREAM_BUFFER_PRELUDE_TIMEOUT_MS`: integer (default: `750`)
- `CODEX_LB_STREAM_BUFFER_PRELUDE_MAX_BYTES`: integer (default: `65536`)

Define “user-visible delta event” as any of:
- `response.output_text.delta`
- `response.output_audio.delta`
- `response.output_audio_transcript.delta`

These correspond to the existing `_TEXT_DELTA_EVENT_TYPES` set in `app/modules/proxy/service.py`.

## Implementation plan (what to change, precisely)

### 1) Plumb buffering settings into the streaming path
- Read `stream_buffer_mode` and limits from `app/core/config/settings.py`.
- In `ProxyService._stream_with_retry`, pass the selected mode/limits into `ProxyService._stream_once`.

### 2) Implement buffering inside `ProxyService._stream_once`
Add a buffering layer that can:
- accumulate SSE lines in a `list[str]` plus a byte counter (UTF-8 byte length of each line),
- decide when to flush buffered lines to the client,
- decide whether a retryable failure occurred before flush (so `_stream_with_retry` can fail over).

Mechanics:
- Keep a local boolean `flushed_to_client`.
  - Only when `flushed_to_client=True` should the caller consider that “emitted any output”.
- For `prelude` mode:
  - Start buffering.
  - End prelude and flush when:
    - the first user-visible delta event type is observed, or
    - a terminal event is observed (flush it; then return), or
    - `PRELUDE_TIMEOUT_MS` elapsed since the first line was received, or
    - buffered bytes exceed `PRELUDE_MAX_BYTES`.
  - If a retryable error occurs before the prelude flush, raise `_RetryableStreamError` without yielding.
  - After flushing, stream-through as today.

### 3) Ensure retryability classification covers the real failure
Your current error is already normalized to `usage_limit_reached`, which is retryable. Keep the optional “Upgrade to
Plus” text/HTML classifier as a hardening step, but treat it as secondary in implementation priority.

## Tests

### Unit tests (must update/add)
- `tests/unit/test_proxy_stream_failover.py`:
  - Add a test for `prelude` mode:
    - first attempt yields `response.created` then raises retryable `ProxyResponseError`,
    - second attempt succeeds,
    - output observed by the client contains no failure and comes only from the successful attempt.
  - Preserve/adjust existing semantics for `off` mode.

- `tests/unit/test_proxy_errors.py` (optional hardening):
  - If adding the text/HTML classifier, add tests for mapping “Upgrade to Plus …” into a retryable code.

## Acceptance criteria
- With `CODEX_LB_STREAM_BUFFER_MODE=prelude`:
  - Failures that occur after `response.created` but before user-visible deltas are retried invisibly when another
    account is available, while retaining mostly-live streaming after the prelude flush.

## Non-goals
- Attempting to “continue” a partially emitted user-visible token stream on a different account (would risk duplicated or
  inconsistent output).
- Changing client behavior; this is proxy-only.
