# Observability: Prometheus Metrics

## Requirements

### Endpoint

- The server MUST expose `GET /metrics`.
- `GET /metrics` MUST return Prometheus text exposition format (`text/plain; version=0.0.4`).
- `GET /metrics` MUST be safe to call frequently and MUST NOT include high-cardinality identifiers (e.g. request IDs).

### Cardinality

- Metrics MUST NOT include labels that create a cross-product of `account_id` and `model`.
- Metrics MUST NOT include labels derived from user prompts, prompt cache keys, or request IDs.

### Metric names and labels

The server MUST export the following metric families (names are normative):

#### Proxy health

- `codex_lb_proxy_requests_total{status,model,api}`
- `codex_lb_proxy_errors_total{error_code}`
- `codex_lb_proxy_retries_total{api,error_class}`
- `codex_lb_proxy_latency_ms_bucket{model,api,le}` (+ `_sum`, `_count`)
- `codex_lb_proxy_tokens_total{kind,model}` where `kind` is one of: `input`, `output`, `cached_input`, `reasoning`
- `codex_lb_proxy_cost_usd_total{model}`
- `codex_lb_proxy_unpriced_success_total{api,reason}` where `reason` is one of:
  - `missing_model`, `missing_usage`, `unknown_pricing`, `unknown`

#### Proxy per-account (no model cross-product)

- `codex_lb_proxy_account_requests_total{account_id,status,api}`
- `codex_lb_proxy_account_tokens_total{account_id,kind,api}` where `kind` is one of: `input`, `output`, `cached_input`, `reasoning`
- `codex_lb_proxy_account_cost_usd_total{account_id,api}`
- `codex_lb_proxy_account_retries_total{account_id,api,error_class}`
- `codex_lb_proxy_account_unpriced_success_total{account_id,api,reason}`
- `codex_lb_proxy_account_errors_total{account_id,error_class}` where `error_class` is one of:
  - `rate_limit`, `quota`, `auth`, `invalid_request`, `upstream`, `internal`, `unknown`

#### Request log buffer

- `codex_lb_request_log_buffer_size`
- `codex_lb_request_log_buffer_dropped_total`

#### Accounts

- `codex_lb_accounts_total{status}`
- `codex_lb_account_identity{account_id,display,plan_type}`

#### Secondary usage (account)

Per-account (label: `{account_id}`):

- `codex_lb_secondary_used_percent{account_id}`
- `codex_lb_secondary_resets_total{account_id}`
- `codex_lb_secondary_reset_at_seconds{account_id}`
- `codex_lb_secondary_window_minutes{account_id}`
- `codex_lb_secondary_remaining_credits{account_id}`
- `codex_lb_proxy_account_cost_usd_7d{account_id}`
- `codex_lb_secondary_used_percent_delta_pp_7d{account_id}`
- `codex_lb_secondary_implied_quota_usd_7d{account_id}`

### Secondary quota estimate semantics (SQLite-derived, 7d)

For a given `account_id`, the following metrics are computed from SQLite and reflect the current secondary weekly cycle
as observed via `usage_history`:

- `codex_lb_proxy_account_cost_usd_7d{account_id}` MUST be the sum of priced proxy request costs in USD since the
  current cycle start, clipped to the last 7 days.
- `codex_lb_secondary_used_percent_delta_pp_7d{account_id}` MUST equal the latest observed secondary `used_percent`
  value for the current cycle, clamped to the range `[0, 100]`.
- `codex_lb_secondary_implied_quota_usd_7d{account_id}` MUST be computed as:
  - `cost_usd_7d / (used_pp_7d / 100)` when `used_pp_7d > 0`
  - `NaN` when `used_pp_7d == 0`

To avoid emitting biased low quota estimates when the provider meter appears to have advanced before codex-lb recorded
any proxy spend for the start of the cycle, the server MUST suppress the 7d quota estimate for an account when all of
the following are true:

- The first `usage_history` sample observed in the current cycle occurs more than a grace window after the inferred
  cycle start, AND
- That first sample's `used_percent` is already materially >0 (percentage points), AND
- There are zero proxy `request_logs` entries in `[cycle_start, first_usage_sample_time)`.

To avoid emitting biased low quota estimates when the provider meter advances by a large amount across a long period of
zero proxy traffic (usage outside codex-lb, or missing logs), the server MUST also suppress the 7d quota estimate for
an account when:

- There exists an interval between two consecutive `usage_history` samples in the current cycle where:
  - `used_percent` increases materially (percentage points), AND
  - The time gap between samples is meaningfully large (hours), AND
  - There are zero proxy `request_logs` entries in `[prev_sample_time, sample_time)`.

When suppressed, the server MUST NOT emit any of:

- `codex_lb_proxy_account_cost_usd_7d{account_id}`
- `codex_lb_secondary_used_percent_delta_pp_7d{account_id}`
- `codex_lb_secondary_implied_quota_usd_7d{account_id}`

#### Load balancer routing

- `codex_lb_lb_select_total{pool,sticky_backend,reallocate_sticky,outcome}` where:
  - `pool` is one of: `pinned`, `full`
  - `sticky_backend` is one of: `db`, `memory`, `none`
  - `reallocate_sticky` is one of: `true`, `false`
  - `outcome` is one of: `selected`, `paused`, `auth`, `paused_or_auth`, `quota_exceeded`, `rate_limited`, `cooldown`, `no_available`, `unknown`
- `codex_lb_lb_mark_total{event,account_id}` where `event` is one of: `rate_limit`, `quota_exceeded`, `permanent_failure`, `error`
- `codex_lb_lb_mark_permanent_failure_total{code}`
- `codex_lb_lb_snapshot_refresh_total`
- `codex_lb_lb_snapshot_updated_at_seconds`
