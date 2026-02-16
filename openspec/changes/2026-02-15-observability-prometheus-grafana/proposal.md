# Proposal: Prometheus + Grafana observability for multi-account usage

## Problem

The built-in dashboard is good for account management and point-in-time summaries, but it does not provide a dense,
trend-oriented view of system behavior across many accounts (usage, waste pacing, resets, request health, latency,
errors, cache efficiency).

Operators need graphs that answer:

- Is secondary waste trending up/down? How far off from ~0 waste are we?
- Are resets clustering (reset cliffs) that create waste risk?
- What is request throughput, latency, error rate, and cache ratio over time?
- Which accounts are driving risk (top projected waste / top delta needed)?

## Goals

- Add a Prometheus scrape endpoint (`GET /metrics`) to export core operational metrics.
- Provide a Grafana dashboard that visualizes multi-account usage and proxy health.
- Keep built-in dashboard for account actions/tables; use Grafana for graphs.
- Keep metric label cardinality bounded (no `account_id` x `model` cross-product).

## Non-goals

- Replacing the built-in dashboard.
- Adding new persistence tables or background jobs.
- High-cardinality per-request labels (request_id, prompt_cache_key, etc.).
