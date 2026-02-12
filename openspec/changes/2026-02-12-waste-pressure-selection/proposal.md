# Proposal: Waste-pressure account selection strategy

## Problem

The proxy currently selects accounts primarily by lowest usage percent, without considering how much
remaining quota is likely to be wasted at the upcoming secondary reset. This can lead to suboptimal
routing decisions when a high-capacity plan (e.g., pro) is approaching secondary reset with large
remaining quota, while low-capacity plans (e.g., free) are chosen instead.

## Goals

- Add a selection strategy that minimizes *total* quota wastage by preferring accounts with the
  highest remaining secondary quota that will reset soonest (waste pressure).
- Keep existing selection behavior unchanged by default.
- Preserve sticky session semantics: strategy influences initial sticky assignment and reallocation
  events, not every request when stickiness is satisfied.
- Add unit tests for the selection strategy.

## Non-goals

- Predict per-request token usage or model-dependent cost to safely drive primary usage to 100%.
- Change sticky session backend/storage behavior.
- Add new dashboard UI settings (this change is configuration-driven).

