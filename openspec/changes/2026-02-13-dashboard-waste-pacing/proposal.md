# Proposal: Dashboard “Zero Waste” pacing for secondary credits

## Problem

The dashboard currently shows remaining quota and reset timers, but it does not answer the operational question:

> At the current consumption rate, are we on track to achieve ~0 secondary-credit waste by reset?

Operators need a single, clear signal for whether secondary credits are likely to expire unused, plus per-account
detail to understand where waste is coming from.

## Goals

- Add a dashboard indicator for “zero-waste pacing” (secondary window only).
- Provide per-account pacing details (current burn rate, required burn rate, projected waste).
- Keep API timestamps as ISO 8601 strings (never epoch values).
- Keep computation pure and testable (core logic in `app/core/`).

## Non-goals

- Predictive modeling beyond the current-window average.
- Primary-window pacing (secondary only).
- Introducing new persistence tables or background jobs.

