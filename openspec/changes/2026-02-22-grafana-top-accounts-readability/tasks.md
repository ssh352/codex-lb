# Tasks

## Dashboard JSON

- [x] Replace panel 29 with `table`+gauge: “Cost yesterday (USD, top 20 accounts)”
  - [x] Time override: `now-2d/d → now/d`
  - [x] Account text colored by `plan_type` (free/plus/pro/unknown), no suffix
  - [x] Value cell uses gradient gauge coloring (`continuous-GrYlRd`)
- [x] Replace panel 31 with `table`+gauge: “Requests yesterday (top 20 accounts)”
  - [x] Same time override
  - [x] Account text colored by `plan_type` (free/plus/pro/unknown), no suffix
  - [x] Value cell uses gradient gauge coloring (`continuous-GrYlRd`)
- [x] Add panel 39: `table`+gauge “Cost (USD, 30d total, top 20 accounts)”
  - [x] Completed days only: `now-30d/d → now/d`
  - [x] Account text colored by `plan_type` (free/plus/pro/unknown), no suffix
  - [x] Value cell uses gradient gauge coloring (`continuous-GrYlRd`)
- [x] Add panel 40: `table`+gauge “Requests (30d total, top 20 accounts)”
  - [x] Completed days only: `now-30d/d → now/d`
  - [x] Account text colored by `plan_type` (free/plus/pro/unknown), no suffix
  - [x] Value cell uses gradient gauge coloring (`continuous-GrYlRd`)
- [x] Shift the “Today so far” row (and below) down to make space for the new 30d panels.

## Validation

- [x] Ensure `observability/grafana/dashboards/codex-lb.json` remains valid JSON (`jq .`).
- [ ] Manual Grafana verification:
  - [ ] Rank panels show 20 accounts and are readable (not overlapped).
  - [ ] Rank values correspond to the last completed day.
  - [ ] 30d panels show 20 accounts and are readable (not overlapped).
  - [ ] Account text color matches `plan_type` and bars remain value-colored.
