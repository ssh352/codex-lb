# Proposal: Make Grafana “Top N accounts” leaderboards configurable (incl. “All”)

## Problem

Several dashboard panels show only the top accounts (e.g. “top 20”). This is good for scanning, but can hide a specific
account that is just below the cutoff (e.g. rank 21), leading to confusion when an operator expects it to appear.

## Goals

- Keep the default leaderboard experience (top 20) fast and scan-friendly.
- Allow switching to “All accounts” without creating duplicate panels.
- Preserve existing `$model`, `$api`, `$account` template variables and identity coloring.

## Non-goals

- Changing Prometheus metrics/labels or exporter behavior.
- Changing dashboard timezone semantics.
- Adding “bottom N” panels.

