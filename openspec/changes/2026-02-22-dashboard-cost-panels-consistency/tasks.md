# Tasks

## Dashboard JSON

- [x] Update panel title + description for the daily cost rollup panel.
- [x] Set `timeTo: "now/d"` on daily rollup panels so they show completed days.
- [x] Update the “Today so far” row layout to 4-up stats (`w=6` each).
- [x] Add “Cost yesterday (USD)” stat panel with the anchored 1d window query.
- [x] Add “Requests yesterday” stat panel with the anchored 1d window query.

## Validation

- [x] Ensure `observability/grafana/dashboards/codex-lb.json` remains valid JSON.
- [x] In Grafana Explore, paste the “Cost yesterday” PromQL and confirm it returns values for daily rollups.
- [ ] Manual Grafana verification (any day, midday):
  - [ ] “Cost today so far” changes during the day.
  - [x] “Cost yesterday” is stable and matches the rightmost bar in the daily “Cost/day” panel.
  - [x] Same correspondence for requests.
  - [ ] Changing browser timezone shifts both “today” and “yesterday” boundaries consistently.
