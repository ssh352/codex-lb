# Notes

## Why this is not a metrics bug

The current dashboard mixes two different time windows:

- Daily rollups: `increase(metric[1d])` at a 1d step, typically interpreted as **completed days** (evaluated on
  midnight boundaries; `[1d]` is a 24-hour window).
- Today-to-date: `timeFrom: now/d` + `increase(metric[$__range])`, which is **since midnight → now**.

Both are correct; the dashboard just needs clearer semantics and a direct “yesterday” comparator.

## Query note: anchoring to midnight without Grafana macros

Avoid relying on `start()` in **instant** queries: depending on the query engine path, `start()` may not refer to the
panel’s `from` time and can effectively behave like “now”.

In this dashboard, the simplest way to make a “yesterday” stat match the daily bar is to reuse the daily query and let
the stat panel reduce to the last point:

- panel time override: `timeFrom: "now-2d/d"`, `timeTo: "now/d"`
- query: `sum(increase(codex_lb_proxy_cost_usd_total[1d]))` with `interval: "1d"`

This yields a small daily time series (e.g. 2–3 points), where the last point corresponds to the same “rightmost bar”
timestamp/value as the daily chart.
