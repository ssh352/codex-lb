# Notes

## Why this is not a metrics bug

The current dashboard mixes two different time windows:

- Daily rollups: `increase(metric[1d])` at a 1d step, typically interpreted as **completed days** (evaluated on
  midnight boundaries; `[1d]` is a 24-hour window).
- Today-to-date: `timeFrom: now/d` + `increase(metric[$__range])`, which is **since midnight → now**.

Both are correct; the dashboard just needs clearer semantics and a direct “yesterday” comparator.

## Query note: anchoring to midnight

Avoid relying on `start()` in **instant** queries: depending on the query engine path, `start()` may not refer to the
panel’s `from` time and can effectively behave like “now”.

This change no longer shows a “yesterday” stat in the “Today” section; the daily rollup panel remains the canonical
source for “completed day” totals.

If a “yesterday” comparator is added later, prefer anchoring with Grafana macros and PromQL `@ <timestamp>` modifiers:

- `sum(increase(codex_lb_proxy_cost_usd_total[1d] @ ${__from:date:seconds}))`

This anchors the “yesterday” window at **today’s midnight** (browser timezone) while the panel range is `now/d → now`.

Fallback (if macro substitution is unavailable in your Grafana/Prometheus path): reuse the daily query and let the stat
panel reduce to the last point:

- panel time override: `timeFrom: "now-2d/d"`, `timeTo: "now/d"`
- query: `sum(increase(codex_lb_proxy_cost_usd_total[1d]))` with `interval: "1d"`

This yields a small daily time series (e.g. 2–3 points), where the last point corresponds to the same “rightmost bar”
timestamp/value as the daily chart.
