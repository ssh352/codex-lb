# Tasks

## Dashboard JSON

- [x] Rename panel `id: 35` title back to `Cost/hr (USD, total, 1h buckets)`.
- [x] Remove projected-EOD line style override from panel `id: 35`.
- [x] Remove projected-EOD series (`refId: C`) from panel `id: 35` targets.

## Validation

- [x] `jq . observability/grafana/dashboards/codex-lb.json` passes.
- [ ] Manual Grafana check: Today chart legend contains only `Cost (USD, 1h)`.
