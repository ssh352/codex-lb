# Design

## Dashboard change

Panel `id: 35` in `observability/grafana/dashboards/codex-lb.json` is simplified:

- Title: `Cost/hr (USD, total, 1h buckets)`
- Description: observed hourly cost buckets since local midnight, with projection explicitly delegated to the dedicated stat panel.
- Keep only one series:
  - `Cost (USD, 1h)`: `sum(increase(codex_lb_proxy_cost_usd_total{model=~"$model"}[1h]))`
- Remove the projected EOD overlay series and its dashed line override.

## Rationale

`Projected EOD cost` is already represented by a dedicated stat panel in the same section. Showing the same signal again
in the hourly cost chart is redundant and can make operators infer trend information from a projection line that is
already summarized clearly in the stat card.

## Failure modes / constraints

- No backend behavior changes; this is a Grafana visualization-only change.
- Cost/hr chart continues to be robust to counter resets by using `increase(...[1h])` buckets.
