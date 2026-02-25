# Design

## Semantics (SSOT)

- **Cost/day**: a 1-day (`[1d]`) window evaluated on midnight boundaries (browser timezone).
  - Interpreted as “completed days” in the dashboard (rightmost bar is **yesterday** for most viewers).
  - Note: `[1d]` is a 24-hour window; on DST transitions this is not identical to a local “calendar day”.
- **Cost today so far**: cost since **today 00:00 (browser TZ) → now**.
- **Projected EOD cost (run-rate)**: a linear projection of “Cost today so far” to a full day: `(cost since midnight) × (24h / elapsed)`.
  - Uses a fixed 86400-second day; on DST transition days this can over/under-estimate vs a local calendar day.

These semantics are intentionally different; the change is to make the dashboard communicate that clearly.

## Grafana dashboard changes

### 1) Clarify the daily rollup panel

Panel: “Cost/day (USD, total + 7d avg)”

- Rename to: `Cost/day (USD, completed days + 7d avg)`
- Set `timeTo: "now/d"` so the panel ends on a midnight boundary (completed days only).
- Add a description:
  - Daily bars are **completed days** (`[1d]` windows evaluated at midnight)
  - The latest bar is usually **yesterday**

### 2) Use a 2-up KPI row for “today”

In the “Today (since 00:00, browser TZ)” row, show two stat panels (`w=12` each):

- Cost — today so far (USD)
- Projected EOD cost (run-rate) (USD)

Panel time override:

- `timeFrom: "now/d"` (midnight in browser TZ → now)

PromQL:

- Cost — today so far:
  - `sum(increase(codex_lb_proxy_cost_usd_total{model=~"$model"}[$__range]))`
- Projected EOD cost (run-rate):
  - `sum(increase(codex_lb_proxy_cost_usd_total{model=~"$model"}[$__range])) * 86400 / clamp_min(vector($__range_s), 60)`

### 3) Replace the in-section cost/hr panel with a “today” rollup chart

Replace the “Cost/hr (USD, total, 1h buckets)” panel in the “Today so far” section with a compact rollup chart that
remains correct across counter resets by using `increase(...[1h])` buckets.

Series:

- Cost (USD, 1h):
  - `sum(increase(codex_lb_proxy_cost_usd_total{model=~"$model"}[1h]))`
- Projected EOD cost (USD) (dashed):
  - `sum(rate(codex_lb_proxy_cost_usd_total{model=~"$model"}[$__rate_interval])) * 86400`

## Constraints / failure modes

- Daily totals assume Prometheus has continuous scrape coverage over the relevant window.
- “Projected EOD cost” assumes the current run-rate is representative; short windows (near midnight) are intentionally clamped.
- Counter resets can distort “since midnight” cumulative series; use `increase(...[1h])` buckets for charts.
- DST transition days can make 86400-second projections diverge from local “calendar day” intuition.
