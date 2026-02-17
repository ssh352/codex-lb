# Design

## New metric families

### Secondary quota (SQLite-derived, 7d)

Secondary `used_percent` is a gauge and can decrease (window reset). For implied quota estimation, the dashboard uses
SQLite-derived 7-day gauges so the result does not depend on Prometheus retention or the proxy process uptime:

- `codex_lb_proxy_account_cost_usd_7d{account_id}`
- `codex_lb_secondary_used_percent_delta_pp_7d{account_id}`
- `codex_lb_secondary_implied_quota_usd_7d{account_id}`

The companion counter `codex_lb_secondary_resets_total{account_id}` remains available as a best-effort signal for reset
detection, but is not required for the 7d estimate.

### Proxy retries

Retry loops can inflate spend and token metrics when errors occur. Export counters for retry attempts:

- `codex_lb_proxy_retries_total{api,error_class}`
- `codex_lb_proxy_account_retries_total{account_id,api,error_class}`

`error_class` matches the existing coarse taxonomy (`rate_limit`, `quota`, `auth`, `invalid_request`, `upstream`,
`internal`, `unknown`).

### Unpriced successful requests

Cost counters are best-effort and can undercount when a “success” response lacks usage tokens or pricing is unknown.
Export counters for successful requests where cost could not be computed:

- `codex_lb_proxy_unpriced_success_total{api,reason}`
- `codex_lb_proxy_account_unpriced_success_total{account_id,api,reason}`

`reason` is one of: `missing_model`, `missing_usage`, `unknown_pricing`, `unknown`.

## PromQL: implied secondary quota (USD)

Prefer the direct gauge:

`codex_lb_secondary_implied_quota_usd_7d`

## Cardinality

- New metrics use `{account_id}` only (optionally plus `{api,error_class}`); no `{account_id,model}` combinations.
- Reasons and error classes are bounded sets.
