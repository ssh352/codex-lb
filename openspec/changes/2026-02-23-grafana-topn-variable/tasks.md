# Tasks

## Dashboard JSON

- [x] Add templating var `accounts_top_n` (custom), default `20`, options include `10000` for “All”.
- [x] Add templating var `accounts_top_n_realtime` (custom), default `10`, for top-N realtime charts.
- [x] Clarify variable labels (“tables” vs “realtime charts”) and add descriptions.
- [x] Update these panels’ PromQL to use `topk($accounts_top_n, ...)`:
  - [x] Panel 29: “Cost yesterday …”
  - [x] Panel 31: “Requests yesterday …”
  - [x] Panel 39: “Cost (USD, 30d total) …”
  - [x] Panel 40: “Requests (30d total) …”
- [x] Update realtime chart panels to use `topk($accounts_top_n_realtime, ...)`:
  - [x] Panel 15: “Top … accounts by cost/hr”
  - [x] Panel 16: “Top … accounts by requests/min”
- [x] Update panel titles/descriptions to avoid hard-coding “top 20”.

## Validation

- [x] `observability/grafana/dashboards/codex-lb.json` remains valid JSON (`jq .`).
- [ ] Manual Grafana verification:
  - [ ] Default `accounts_top_n=20` matches prior behavior.
  - [ ] Setting `accounts_top_n=10000` includes accounts previously just below top-20.
  - [ ] Default `accounts_top_n_realtime=10` matches prior behavior.
