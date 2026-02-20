# Design: Load Balancer Debug Introspection

## Constraints

- Debug endpoints MUST be **disabled by default** and MUST NOT appear in OpenAPI schema.
- When enabled, endpoints are intended for **localhost debugging**; responses may include PII (email).
- Always-on targeted logs SHOULD prefer human-readable account identity without the full random-looking account ID.
- Debug surfaces MUST NOT expose raw sticky keys or prompt-derived identifiers.
- Selection-history storage MUST be bounded and MUST NOT require DB writes on the proxy hot path.
- Time values MUST be exposed as ISO 8601 timestamps (JSON datetimes), not epoch numbers.

## Settings

Add settings to `app/core/config/settings.py`:

- `debug_endpoints_enabled: bool = False` (`CODEX_LB_DEBUG_ENDPOINTS_ENABLED`)
- `debug_lb_event_buffer_size: int = 1000` (`CODEX_LB_DEBUG_LB_EVENT_BUFFER_SIZE`, `gt=0`)

## Debug endpoints

Add a new router `app/modules/debug/api.py` (all routes `include_in_schema=False`), and conditionally mount it from
`app/main.py` only when `debug_endpoints_enabled` is true.

When disabled, routes MUST behave as if they do not exist (404) to reduce accidental discovery.

### `GET /debug/lb/state`

Purpose: “Why is account X not eligible right now?”

Response includes:

- server time (UTC)
- pinned account IDs
- sticky backend + sticky distribution summary (counts per account; no raw keys)
- per-account rows with:
  - identity: `account_id`, `email`, `plan_type`
  - persisted: `status`, `deactivation_reason`, `reset_at` (if any)
  - latest usage snapshots: primary + secondary (`used_percent`, `reset_at`, `window_minutes`)
  - runtime (in-memory): `cooldown_until`, `last_error_at`, `last_selected_at`, `error_count`
  - computed eligibility:
    - `eligible_in_pinned_pool` + `ineligible_reason_in_pinned_pool`
    - `eligible_in_full_pool` + `ineligible_reason_in_full_pool`

### `GET /debug/lb/events?limit=200`

Purpose: “What happened recently and did we fall back pinned → full?”

Return the most recent selection attempts (newest first), bounded by `limit` and the ring buffer capacity.

Each event includes:

- `ts` (UTC datetime)
- `request_id` (if present in context)
- `pool` attempted (`pinned` or `full`)
- `sticky_backend` + `reallocate_sticky`
- `outcome` (selected/no-available/etc) and `reason_code`
- `selected_account_id` if selected
- `error_message` if not selected
- `fallback_from_pinned` boolean for “full pool attempt after pinned failure”

## Ring buffer for selection events

Implement an in-memory `deque(maxlen=debug_lb_event_buffer_size)` owned by `LoadBalancer`:

- Append an event for every selection attempt.
- When pinned routing is active and a pinned attempt fails, record the pinned attempt and the subsequent full attempt
  with `fallback_from_pinned=true`.
- Attach request IDs via `app/core/utils/request_id.get_request_id()` when present.

## Computing ineligibility reasons (authoritative “why”)

Add a helper that mirrors `select_account()` availability filtering but **does not mutate state**, returning either:

- `eligible=True`
- or `eligible=False` with a stable `reason` string (e.g. `paused`, `deactivated`, `rate_limited`, `quota_exceeded`,
  `cooldown`, `error_backoff`)

This is required because `select_account()` can auto-clear some conditions by mutating the state, which would make a
debug view racey or misleading.

## Always-on targeted logs (low noise)

The proxy should emit a small number of structured log lines to allow postmortems without enabling debug endpoints:

1) **Pinned failure → full fallback** (only when pinned routing is active and the pinned attempt yields no selection):
   - include: `request_id` (if present), pinned pool size, pinned attempt outcome (`reason_code`/error_message),
     and a compact breakdown of ineligibility reasons across pinned candidates (counts only).
   - identify selected accounts as `email` plus an `account_id_short` prefix (first 3 chars) rather than full IDs.
   - throttle logging so repeated pinned failures do not produce a log line per request.

2) **Mark events that affect eligibility**:
   - on `mark_rate_limit` and `mark_usage_limit_reached`, log `email` + `account_id_short`, error code class,
     `error_count`, and the computed `cooldown_until` (and `reset_at` when known).
   - these events are rare compared to total requests and are high-signal for “why did this account stop being used?”.

## Schemas

Add strict Pydantic response models under `app/modules/debug/schemas.py`:

- No `dict`/`object` payloads when shapes are known.
- All times are `datetime | None` (UTC in practice).
- IDs and enums are strings.
- Lists for repeated items for stable JSON ordering.

## Notes (operational)

- This debug surface is expected to be used ad-hoc during incidents; it should be fast and safe.
- The ring buffer is intentionally non-persistent; `request_logs` remains the durable record of outcomes, while debug
  endpoints provide the missing “decision inputs” and “recent trail”.
