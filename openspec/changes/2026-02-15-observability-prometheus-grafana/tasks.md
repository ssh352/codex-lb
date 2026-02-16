# Tasks

- [ ] Add OpenSpec capability `openspec/specs/observability-prometheus-metrics/{spec.md,context.md}`.
- [ ] Add dependency `prometheus-client`.
- [ ] Implement metrics registry module under `app/core/metrics/`.
- [ ] Add `GET /metrics` route and include it in `app/main.py`.
- [ ] Instrument proxy request completion to record:
  - [ ] requests_total
  - [ ] latency histogram
  - [ ] tokens_total
  - [ ] cost_usd_total
  - [ ] errors_total
  - [ ] request log buffer size/drops
- [ ] Implement per-account secondary gauges + aggregates (usage + waste pacing).
- [ ] Wire gauge refresh into:
  - [ ] usage refresh scheduler
  - [ ] dashboard overview service
- [ ] Add `docker-compose.observability.yml` + Prometheus config + Grafana provisioning + dashboard JSON.
- [ ] Add unit tests for metrics updates.
- [ ] Add integration test for `/metrics`.
