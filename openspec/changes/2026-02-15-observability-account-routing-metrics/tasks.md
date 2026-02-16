# Tasks

- [ ] Extend `ProxyRequestObservation` to include `account_id` (optional).
- [ ] Add proxy per-account metric families to `app/core/metrics/metrics.py`.
- [ ] Thread `account_id` through proxy instrumentation sites (stream + compact paths).
- [ ] Add load balancer metric families to `app/core/metrics/metrics.py`.
- [ ] Instrument `app/modules/proxy/load_balancer.py`:
  - [ ] record select attempts (pinned + full) with outcome codes
  - [ ] record mark events (rate_limit/quota_exceeded/permanent_failure/error)
  - [ ] record snapshot refresh + updated-at
- [ ] Extend `observability/grafana/dashboards/codex-lb.json` with:
  - [ ] top accounts by cost/hr and requests/sec
  - [ ] routing outcomes and mark event panels
- [ ] Add/extend unit tests for new metric families.
