# Design: Per-account + Routing Metrics

## Constraints

- Avoid high cardinality:
  - MUST NOT create `{account_id, model}` cross-product labels.
  - `account_id` is acceptable as a label only when not combined with `model`.

## Proxy per-account metrics

Update the proxy completion path to emit per-account counters alongside the existing global metrics.

### Families

- `codex_lb_proxy_account_requests_total{account_id,status,api}`
- `codex_lb_proxy_account_tokens_total{account_id,kind,api}`
- `codex_lb_proxy_account_cost_usd_total{account_id,api}`
- `codex_lb_proxy_account_errors_total{account_id,error_class}`

### `error_class`

`error_class` is a coarse, bounded label intended to remain stable even when upstream error codes evolve:

- `rate_limit` (429 / rate limit codes)
- `quota` (insufficient_quota / usage_not_included / quota_exceeded)
- `auth` (refresh failures, invalid credentials)
- `invalid_request` (bad payload/parameters)
- `upstream` (5xx or upstream failures)
- `internal` (codex-lb internal errors)
- `unknown` (fallback)

## Load balancer routing metrics

Instrumentation lives in `LoadBalancer` to capture both selection behavior and account state marks.

### Families

- `codex_lb_lb_select_total{pool,sticky_backend,reallocate_sticky,outcome}`
  - `pool`: `pinned` for the pinned-only attempt, `full` for full-pool selection.
  - `sticky_backend`: `db`, `memory`, `none` (derived from settings + sticky_key presence).
  - `reallocate_sticky`: `true` when a retry explicitly reassigns a sticky key.
  - `outcome`: bounded “why we did/didn’t pick an account”.
- `codex_lb_lb_mark_total{event,account_id}`
- `codex_lb_lb_mark_permanent_failure_total{code}`
- `codex_lb_lb_snapshot_refresh_total`
- `codex_lb_lb_snapshot_updated_at_seconds`

### Selection outcome codes

`outcome` is one of:

- `selected`
- `paused`
- `auth`
- `paused_or_auth`
- `quota_exceeded`
- `rate_limited`
- `cooldown`
- `no_available`
- `unknown`
