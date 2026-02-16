# Proposal: Per-account + Routing Metrics (Prometheus/Grafana)

## Problem

The existing Prometheus metrics answer global health questions (requests/errors/latency/cost) and secondary waste pacing,
but they do not make it easy to:

- spot “which account is burning cost/tokens” quickly
- understand routing behavior (pinned pool fallbacks, sticky reallocation, why selection fails)

## Goals

- Export bounded-cardinality **per-account traffic** metrics (requests/tokens/cost + coarse error classes).
- Export bounded-cardinality **load balancer routing** metrics (selection outcomes + mark events).
- Extend the Grafana dashboard to surface “top-N accounts” and “routing outcome” panels.

## Non-goals

- No metrics that create a label cross-product of `{account_id, model}`.
- No metrics with request IDs, cache keys, or prompt-derived labels.

## Acceptance criteria

- `GET /metrics` exports the metric families added in `openspec/specs/observability-prometheus-metrics/spec.md`.
- Routing selection attempts and mark events are visible in Grafana with useful top-N views.
