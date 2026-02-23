# Proposal: Make “Cost/day” vs “Cost today so far” unambiguous in Grafana

## Problem

The Grafana dashboard shows:

- **Cost/day (USD, total + 7d avg)** as a daily timeseries using `increase(...[1d])` at 1d resolution.
- **Cost today so far (USD)** as a stat using `timeFrom: now/d` and `increase(...[$__range])`.

Operators frequently expect the “today” number in the daily panel to match “Cost today so far”, but they usually do
not, because the panels represent different windows:

- the daily panel’s rightmost bar is typically the **last completed day** (yesterday), while
- the “today so far” stat is **since local midnight → now**.

This ambiguity causes confusion and makes it harder to spot real spend anomalies.

## Goals

- Keep daily rollups correct and easy to interpret.
- Make it obvious what the daily panel’s “latest” bar corresponds to.
- Provide a **“yesterday”** stat that matches the latest daily bar.

## Non-goals

- Changing the underlying Prometheus metrics (`codex_lb_proxy_cost_usd_total`, etc.).
- Introducing a new backend-derived daily-rollup metric.
- Changing the global dashboard timezone away from `browser`.
