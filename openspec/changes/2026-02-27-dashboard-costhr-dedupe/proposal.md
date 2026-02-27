# Proposal: Dedupe Today cost chart vs Projected EOD panel

## Problem

The Grafana "Today" section currently includes:

- A dedicated stat panel: `Projected EOD cost (run-rate)`.
- A timeseries chart that also overlays `Projected EOD cost (USD)` on top of hourly cost bars.

This duplicates the same projection signal in two panels and adds visual noise to the hourly cost chart.

## Goals

- Keep `Projected EOD cost (run-rate)` as the single source of truth for projection.
- Make the chart directly answer a single question: hourly cost since midnight.
- Reduce legend and styling complexity in the Today chart.

## Non-goals

- Changing Prometheus metric names or backend metric emission.
- Changing the projected-EOD stat panel formula.
- Changing dashboard timezone semantics.
