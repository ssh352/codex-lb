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

### Grafana admin password reset (docker)

Purpose: reset the Grafana admin password when it is unknown or has been changed in the persisted data volume.
This is an operational runbook step for the local docker-compose setup; it updates the SQLite DB under
`/var/lib/grafana` and preserves dashboards.

Constraints:
- Requires the Grafana container to be running and accessible via Docker.
- Container name assumes the default compose name (`codex-lb-grafana-1`); adjust if you renamed the project.

Failure modes:
- If the container name is wrong or Grafana is not running, the command fails. Confirm with `docker ps` and use the
  correct container name.

Example (reset to `admin`):

```bash
docker exec codex-lb-grafana-1 grafana cli admin reset-admin-password admin
```

### Selective model cleanup caveat (TSDB tombstones)

If you need to remove one model label from Prometheus history (for example `model="gpt-4.1-mini"`), keep in mind:

- `delete_series` is tombstone-based, not an immediate physical purge of all index metadata.
- You can see zero active samples for that model in instant/range queries while `/series` or `label_values`
  still return historical labelsets for some time.
- If the exporter keeps emitting that label, Prometheus will ingest it again on the next scrape unless you
  block it with `metric_relabel_configs`.

Operational verification should prioritize active query behavior:

- Pass: instant/range queries for `{model="gpt-4.1-mini"}` return no active samples.
- Non-blocking: `/series` or `label_values` may still show historical metadata until compaction/retention.

Enable Prometheus admin API only for cleanup windows, then disable it again.

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
For low-consumption accounts (very small secondary % movement), the estimate is inherently unstable and can be dominated
by rounding and scrape noise; prefer a longer lookback window and/or suppress the metric unless secondary consumption
exceeds a minimum number of percentage points.

To avoid dependence on Prometheus retention and process uptime, codex-lb exports SQLite-derived 7d gauges for this
estimate:

- `codex_lb_proxy_account_cost_usd_7d{account_id}`: spend in USD since the most recent weekly secondary reset (as
  observed in `usage_history`), clipped to the last 7 days. This is computed from SQLite `request_logs` (token sums ×
  pricing) over the inferred cycle range; it does not include any usage that occurred outside codex-lb.
- `codex_lb_secondary_used_percent_delta_pp_7d{account_id}`: secondary used% consumption (percentage points) since the
  most recent weekly secondary reset (as observed in `usage_history`), clipped to the last 7 days. In practice this is
  equivalent to the latest observed `used_percent` for the current cycle (clamped to `[0, 100]`). It is not derived
  from per-request logs; it comes from the latest `usage_history` snapshot.
- `codex_lb_secondary_implied_quota_usd_7d{account_id}`: the derived ratio (`spend / (delta_pp/100)`), set to `NaN`
  when `delta_pp == 0`.

When an account’s secondary meter has already advanced materially in the current cycle but there are no proxy request
logs near the cycle start, the implied quota estimate can be misleading (spend is incomplete relative to the meter
movement). codex-lb may suppress the 7d quota estimate for such accounts.

More precisely, codex-lb suppresses the 7d quota estimate when it observes a likely cycle-start mismatch:

- Let `cycle_start = reset_at - window_minutes*60` for the current cycle (as inferred from the latest `usage_history`
  snapshot).
- Let `(t0, u0)` be the first `usage_history` sample time/value observed in that same cycle.
- Let `n0` be the number of `request_logs` rows in `[cycle_start, t0)`.

If `(t0 - cycle_start)` is larger than a grace window (to tolerate restarts / delayed sampling), `u0` is already
materially >0 (e.g. ≥5pp), and `n0 == 0`, then the provider meter advanced without any proxy spend being recorded for
that early interval. This does not prove "external usage" as fact (codex-lb could have been down or misconfigured), but
it *does* mean `spend / (used_pp/100)` would be biased low, so codex-lb suppresses the estimate instead of emitting a
misleading weekly quota.

Operationally, this is why two accounts can differ:

- Account A (e.g. `bch…`) at `60pp` is still estimable: codex-lb has request logs covering the same cycle interval as the
  meter movement, so the estimate is an extrapolation: `quota ≈ spend_since_reset / 0.60`.
- Account B (e.g. `ve…`) with a first observed cycle sample already at `18pp` but no request logs before that sample is
  suppressed: the denominator includes early-cycle usage that the numerator cannot include, so the implied quota would
  be a lower bound rather than a good estimate.

codex-lb may also suppress the estimate when it observes a mid-cycle mismatch: a large `used_percent` jump over a long
gap where codex-lb recorded zero proxy `request_logs` in the same interval. This is a strong indicator that the meter
advanced without matching proxy spend being recorded, so `spend / (used_pp/100)` would again be biased low.

- Implied secondary quota (USD) by account (7 day window, SSOT):
  - Prefer the direct gauge:
    - `codex_lb_secondary_implied_quota_usd_7d`
  - Or compute it from inputs:
    - `codex_lb_proxy_account_cost_usd_7d / (codex_lb_secondary_used_percent_delta_pp_7d / 100)`
  - Suppress “thin-signal” accounts (e.g. require ≥ 5pp movement):
    - `(codex_lb_secondary_implied_quota_usd_7d and on(account_id) (codex_lb_secondary_used_percent_delta_pp_7d >= 5))`

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
- Selected tier share/sec:
  - `sum by (tier) (rate(codex_lb_lb_selected_tier_total[5m]))`
- Tier score p95 (per tier):
  - `histogram_quantile(0.95, sum by (le, tier) (rate(codex_lb_lb_tier_score_bucket[5m])))`
- Mark events/sec:
  - `sum by (event) (rate(codex_lb_lb_mark_total[5m]))`
- Permanent failures by code:
  - `sum by (code) (increase(codex_lb_lb_mark_permanent_failure_total[1h]))`
