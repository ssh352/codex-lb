# Design

## New metric families

### Secondary percent consumption (monotonic)

Secondary `used_percent` is a gauge and can decrease (window reset). To enable robust “percent consumed” deltas in
PromQL, export a monotonic counter that increments on positive progress and handles weekly reset boundaries:

- `codex_lb_secondary_used_percent_increase_total{account_id}`
- `codex_lb_secondary_resets_total{account_id}`

Reset detection uses the `secondary_reset_at_epoch` timestamp: if it advances by more than 1 hour between samples,
the window is treated as reset and the counter increments by `(100 - prev_used) + cur_used`.

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

Given a time range `$range` (e.g. `1d`), implied quota is:

```
increase(codex_lb_proxy_account_cost_usd_total[$range])
/
(increase(codex_lb_secondary_used_percent_increase_total[$range]) / 100)
```

This is intentionally “implied” (depends on workload/model mix and cache ratio).

## Cardinality

- New metrics use `{account_id}` only (optionally plus `{api,error_class}`); no `{account_id,model}` combinations.
- Reasons and error classes are bounded sets.

