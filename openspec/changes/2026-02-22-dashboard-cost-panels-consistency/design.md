# Design

## Semantics (SSOT)

- **Cost/day**: a 1-day (`[1d]`) window evaluated on midnight boundaries (browser timezone).
  - Interpreted as “completed days” in the dashboard (rightmost bar is **yesterday** for most viewers).
  - Note: `[1d]` is a 24-hour window; on DST transitions this is not identical to a local “calendar day”.
- **Cost today so far**: cost since **today 00:00 (browser TZ) → now**.
- **Cost yesterday** (new): a 1-day (`[1d]`) window evaluated at **today’s midnight** (browser timezone), designed to
  match the daily panel’s latest bar.

These semantics are intentionally different; the change is to make the dashboard communicate that clearly and provide
an explicit “yesterday” comparator.

## Grafana dashboard changes

### 1) Clarify the daily rollup panel

Panel: “Cost/day (USD, total + 7d avg)”

- Rename to: `Cost/day (USD, completed days + 7d avg)`
- Set `timeTo: "now/d"` so the panel ends on a midnight boundary (completed days only).
- Add a description:
  - Daily bars are **completed days** (`[1d]` windows evaluated at midnight)
  - The latest bar is usually **yesterday**
  - Compare with “Cost yesterday (USD)”

### 2) Add “yesterday” stat panels (cost + requests)

In the “Today so far” row, show four stats:

- Cost today so far (USD)
- Cost yesterday (USD)
- Requests today so far
- Requests yesterday

PromQL for **yesterday** stats should use the same `[1d]` daily rollup query as the daily panel, but shown in a stat
panel via a short (2-day) panel time override so the stat’s reduction picks the last completed day. This avoids
relying on `start()` semantics in instant queries and avoids `$__range` edge cases around rounded time ranges.

- Cost yesterday:
  - Panel time override: `timeFrom: "now-2d/d"`, `timeTo: "now/d"`
  - Target: `sum(increase(codex_lb_proxy_cost_usd_total{model=~"$model"}[1d]))` with `interval: "1d"`
- Requests yesterday:
  - Panel time override: `timeFrom: "now-2d/d"`, `timeTo: "now/d"`
  - Target: `sum(increase(codex_lb_proxy_requests_total{model=~"$model",api=~"$api"}[1d]))` with `interval: "1d"`

Implementation note:

- Use `hideTimeOverride: true` so the “yesterday” panels are stable and do not depend on the dashboard’s global range.

## Constraints / failure modes

- “Yesterday” stats assume Prometheus has continuous scrape coverage over the full `now-2d/d → now/d` window.
- Daily/yesterday totals assume Prometheus has continuous scrape coverage over the relevant window.
