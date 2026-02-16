## Context

### Why Prometheus/Grafana

Prometheus provides a stable time-series store for operational signals. Grafana provides dense, configurable
visualizations without growing a custom frontend for charts.

The built-in dashboard remains the best place for:

- account actions (pause/resume/reauth)
- tables and snapshots

Grafana is the best place for:

- trends (waste pacing, request health, latency, cache ratio)
- top-N risk tables
- reset cliff detection

### Running with docker-compose

Recommended:

- Compose plugin: `docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d`
- Standalone binary: `docker-compose -f docker-compose.yml -f docker-compose.observability.yml up -d`

Grafana should be bound to localhost only by default.

### Suggested panels (PromQL)

- Top 20 accounts by remaining secondary credits:
  - `topk(20, codex_lb_secondary_remaining_credits)`
- Top 20 accounts by secondary used percent:
  - `topk(20, codex_lb_secondary_used_percent)`
- Error rate:
  - `sum(rate(codex_lb_proxy_requests_total{status="error"}[5m])) / sum(rate(codex_lb_proxy_requests_total[5m]))`
- Latency p95:
  - `histogram_quantile(0.95, sum by (le) (rate(codex_lb_proxy_latency_ms_bucket[5m])))`
- Cache ratio:
  - `sum(rate(codex_lb_proxy_tokens_total{kind="cached_input"}[5m])) / sum(rate(codex_lb_proxy_tokens_total{kind="input"}[5m]))`

#### Per-account traffic

- Top 10 accounts by cost/hr:
  - `topk(10, sum by (account_id) (rate(codex_lb_proxy_account_cost_usd_total[5m])) * 3600)`
- Top 10 accounts by requests/sec:
  - `topk(10, sum by (account_id) (rate(codex_lb_proxy_account_requests_total[5m])))`
- Error rate by account:
  - `sum by (account_id) (rate(codex_lb_proxy_account_requests_total{status="error"}[5m])) / sum by (account_id) (rate(codex_lb_proxy_account_requests_total[5m]))`

#### Account identity (email vs. id)

Prometheus metrics use `account_id` for stable identity. For operator-friendly dashboards, the server also exports:

- `codex_lb_account_identity{account_id,display} 1`

`display` is controlled by `CODEX_LB_METRICS_ACCOUNT_IDENTITY_MODE`:

- `email` (default): `display=email`
- `account_id`: `display=account_id` (no PII)

To display `display` alongside account metrics in PromQL, join on `account_id`:

- `... * on(account_id) group_left(display) codex_lb_account_identity`

#### Retries + estimation quality

- Retries/sec (by error class):
  - `sum by (error_class) (rate(codex_lb_proxy_retries_total[5m]))`
- Top 10 accounts by retries/sec:
  - `topk(10, sum by (account_id) (rate(codex_lb_proxy_account_retries_total[5m])))`
- Unpriced “success” responses/sec (under-count risk):
  - `sum by (reason) (rate(codex_lb_proxy_unpriced_success_total[5m]))`
- Top 10 accounts by unpriced “success”/sec:
  - `topk(10, sum by (account_id) (rate(codex_lb_proxy_account_unpriced_success_total[5m])))`

#### Secondary quota (implied, USD)

“Implied” quota is computed from observed spend divided by observed secondary percent consumption over the same range.
This is workload-dependent (model mix + caching), so treat it as an operational estimate.

`codex_lb_secondary_used_percent` is a gauge and can decrease on window reset. Use the monotonic counter
`codex_lb_secondary_used_percent_increase_total` for deltas; it increments on positive progress and treats a
weekly reset as `(100 - prev_used) + cur_used` when the `reset_at` timestamp advances materially. The companion
counter `codex_lb_secondary_resets_total` counts detected resets.

- Implied secondary quota (USD) by account (1 day window):
  - `increase(codex_lb_proxy_account_cost_usd_total[1d]) / (increase(codex_lb_secondary_used_percent_increase_total[1d]) / 100)`

#### Activity by hour

- Cost per hour for an account:
  - `increase(codex_lb_proxy_account_cost_usd_total{account_id="$account"}[1h])`
- Requests per hour for an account:
  - `increase(codex_lb_proxy_account_requests_total{account_id="$account"}[1h])`

#### Daily totals (Prometheus-only)

If Prometheus has been scraping `/metrics` continuously, you can get per-day rollups without any SQLite-derived metrics.

For “calendar day” totals, set the Grafana dashboard time range to whole-day boundaries (e.g. `now-7d/d` to `now/d`)
and use a 1-day query step (Grafana “Min step” / `interval=1d`) so evaluation timestamps land on midnight boundaries.

- Total cost per day:
  - `sum(increase(codex_lb_proxy_cost_usd_total[1d]))`
- Cost per day by account (top N):
  - `topk(20, sum by (account_id) (increase(codex_lb_proxy_account_cost_usd_total[1d])))`

#### Routing behavior

- Selection outcomes/sec (stacked):
  - `sum by (pool, outcome) (rate(codex_lb_lb_select_total[5m]))`
- Mark events/sec:
  - `sum by (event) (rate(codex_lb_lb_mark_total[5m]))`
- Permanent failures by code:
  - `sum by (code) (increase(codex_lb_lb_mark_permanent_failure_total[1h]))`
