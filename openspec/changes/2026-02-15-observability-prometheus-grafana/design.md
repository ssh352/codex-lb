# Design

## High-level approach

- Add a new FastAPI route: `GET /metrics` returning Prometheus text exposition.
- Instrument the proxy request completion path to export request counters, latency histograms, tokens, and cost.
- Periodically refresh per-account usage/waste gauges from the latest `UsageHistory` snapshots and account metadata.
- Provide an optional docker-compose add-on that runs Prometheus + Grafana with provisioned datasources/dashboards.

## Interfaces

### HTTP

- `GET /metrics`
  - MUST return `text/plain; version=0.0.4`.
  - MUST be safe to call frequently.

### Docker Compose

Add `docker-compose.observability.yml`:

- `prometheus` service
  - Scrapes `codex-lb` at `/metrics`.
  - Stores time-series in a local volume.
- `grafana` service
  - Uses a provisioned Prometheus datasource.
  - Loads a provisioned `codex-lb` dashboard JSON.

Default deployment is local-only (Grafana bound to 127.0.0.1).

## Metric model and cardinality

- Request metrics: labeled by `{model}` and `{api}` and coarse `{status}` only.
- Per-account metrics: labeled by `{account_id}` only.
- DO NOT emit metrics with labels `{account_id, model}` together.

Target scale: <= ~300 accounts.

## Core metrics

### Proxy request health (no account labels)

- Counter: `codex_lb_proxy_requests_total{status,model,api}`
- Counter: `codex_lb_proxy_errors_total{error_code}`
- Histogram: `codex_lb_proxy_latency_ms_bucket{model,api,le}` (+ _sum/_count)
- Counter: `codex_lb_proxy_tokens_total{kind,model}` where kind in {input, output, cached_input, reasoning}
- Counter: `codex_lb_proxy_cost_usd_total{model}`

### Request log buffer

- Gauge: `codex_lb_request_log_buffer_size`
- Counter: `codex_lb_request_log_buffer_dropped_total`

### Secondary usage gauges (per-account + counters)

Per-account (label: `{account_id}`):

- `codex_lb_secondary_used_percent`
- `codex_lb_secondary_used_percent_increase_total`
- `codex_lb_secondary_resets_total`
- `codex_lb_secondary_reset_at_seconds`
- `codex_lb_secondary_window_minutes`
- `codex_lb_secondary_remaining_credits`

### Account status counts

- Gauge: `codex_lb_accounts_total{status}`

## Update strategy

- Update request metrics inline on request completion.
- Update per-account gauges:
  - after each usage refresh cycle, and
  - during dashboard overview generation (keeps gauges fresh even if refresh loop is disabled).

## Grafana dashboard

A provisioned dashboard with panels:

- Secondary usage (percent + remaining credits)
- Account status counts
- Requests/sec by model
- Error rate
- Latency p50/p95
- Cache ratio
- Reset cliff: time-to-reset (table)
