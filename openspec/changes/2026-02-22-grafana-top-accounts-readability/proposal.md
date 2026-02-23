# Proposal: Make “Top 20 accounts” daily cost/requests readable in Grafana

## Problem

The dashboard panels:

- **Cost/day (USD, top 20 accounts)**
- **Requests/day (top 20 accounts)**

are hard to read because they plot ~20 overlapping time series, producing a “spaghetti chart” with an oversized legend.
Operators struggle to answer two common questions quickly:

1) **Who are the biggest accounts yesterday?** (ranking/comparison)
2) **How did each top account behave over the last 30 days?** (trend/patterns)

## Goals

- Provide a fast, scannable **top-20 “yesterday” ranking** for both cost/day and requests/day.
- Provide a **30d trend view** that shows per-account daily behavior without overlapping lines.
- Keep “daily rollups” semantics aligned with the existing **completed days** convention (`timeTo: now/d`).
- Preserve the existing `$model`, `$api`, `$account` template variables and the “(selected)” drilldown panels.

## Non-goals

- Changing Prometheus metrics, labels, or exporters.
- Adding backend-derived “daily rollup” metrics.
- Changing the global dashboard timezone away from `browser`.

