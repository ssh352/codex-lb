# Proposal: Single waste-pressure account selection

## Problem

Account selection currently supports multiple strategies (`usage`, `reset_bucket`, `waste_pressure`)
and a dashboard toggle (`prefer_earlier_reset_accounts`) that implicitly changes behavior. This adds
operational ambiguity and can produce unintuitive routing when secondary usage/reset data is missing.

## Goals

- Make **waste-pressure** the single, always-on account selection algorithm.
- Remove `CODEX_LB_PROXY_SELECTION_STRATEGY` and the other selection behaviors.
- Ensure missing secondary data does not cause incorrect routing:
  - Accounts with unknown `secondary_reset_at` must not be treated as urgent for waste minimization.
  - Accounts with unknown `secondary_used_percent` must not be treated as 0% used.
- Preserve sticky-session semantics (selection only affects initial assignment and reallocation).
- Keep the system stable under rate limits by rerouting to other accounts when possible.

## Non-goals

- Predict per-request token usage or model-dependent cost.
- Introduce new UI toggles for selection strategy.

