# Tasks

1. Update Prometheus metrics spec to include:
   - secondary percent consumption counters
   - proxy retry counters
   - unpriced-success counters
2. Implement new metric families in `app/core/metrics/metrics.py`.
3. Instrument proxy retry loops in `app/modules/proxy/service.py`.
4. Emit secondary percent consumption counters from the usage refresh cycle.
5. Update `observability/grafana/dashboards/codex-lb.json` with panels for:
   - retries/sec (overall)
   - implied secondary quota (USD) per account (table)
6. Add/adjust tests covering the new metric output.

