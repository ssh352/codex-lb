# Proposal: Tune default HTTP keep-alive timeout for proxy stability

## Problem

Some deployments run codex-lb behind proxy chains (e.g. VLESS/xray) and/or NATs that can silently
drop idle keep-alive connections. When aiohttp later reuses a stale pooled socket, requests can fail
at startup with transport errors such as:

- `Connection reset by peer` (macOS `[Errno 54]` / `ECONNRESET`)

This is not an application-layer upstream error, but it degrades perceived reliability.

## Goals

- Reduce the probability of reusing stale idle connections by tuning the default aiohttp connector
  idle keep-alive timeout.
- Clarify what the setting does and how to tune it.

## Non-goals

- Eliminating mid-stream resets (requires proxy/network tuning and/or concurrency caps).
- Adding proxy-specific dependencies.

