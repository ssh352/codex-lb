# Proposal: Quota + Retry Observability Metrics

## Problem

Today we can see per-account spend and secondary used percent, but it is difficult to:

- estimate “implied” secondary window quota (USD) from observed spend vs. secondary usage percentage deltas
- distinguish genuine spend from spend amplified by retry loops (e.g. upstream/network instability)
- quantify how much spend is *missing* from counters due to “successful” requests with missing usage/pricing

## Goals

- Export low-cardinality metrics that support:
  - implied secondary window quota (USD) calculations in PromQL/Grafana
  - retry rate monitoring (overall and per-account)
  - visibility into undercounted spend due to unpriced successful requests

## Non-goals

- No labels that create a cross-product of `{account_id, model}`.
- No labels derived from request IDs, prompts, or user content.
- No attempt to de-duplicate retries semantically (requires idempotency keys / payload hashing).

## Acceptance criteria

- `GET /metrics` exports the new metric families listed in the Prometheus metrics spec.
- Proxy retry loops increment retry metrics with coarse error classification.
- Secondary usage refresh emits a monotonic “secondary percent consumed” counter that is safe across weekly resets.
- Grafana dashboard includes panels for retry rate and implied secondary quota (USD).

