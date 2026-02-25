# Tasks

## Dashboard JSON

- [x] Update panel title + description for the daily cost rollup panel.
- [x] Set `timeTo: "now/d"` on daily rollup panels so they show completed days.
- [x] Replace the “Today so far: KPIs” list panel with a 2-up KPI row (cost + projected EOD cost).
- [x] Remove requests from the “Today” KPI row and chart.
- [x] Replace the in-section cost/hr panel with a “today” rollup chart (cost/hr + projected EOD cost).

## Validation

- [x] Ensure `observability/grafana/dashboards/codex-lb.json` remains valid JSON.
- [ ] Manual Grafana verification (any day, midday):
  - [ ] “Cost — today so far” changes during the day.
  - [ ] “Projected EOD cost (run-rate)” renders dashed and changes during the day.
  - [ ] Chart legend includes only cost/hr + projected EOD cost.
  - [ ] Changing browser timezone shifts the “today” boundary consistently.
