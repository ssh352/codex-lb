# Proposal: Improve proxy hot-path throughput and usage refresh scalability

## Problem

- Under concurrency, SQLite write contention on proxy hot paths (sticky sessions + request logging) can
  drive high tail latencies.
- With many accounts (e.g. 100), sequential usage refresh can become too slow and increases time
  spent inside a single refresh loop.

## Goals

- Avoid SQLite writes on the proxy request hot path where possible (single-instance mode).
- Make usage refresh scale to O(100) accounts without turning refresh into a multi-minute loop.

## Non-goals

- Multi-instance / horizontally scaled deployments (still supported, but may require DB-backed
  stickiness).
- SQLite → Postgres migration (separate operational decision).

