# Proposal: Reset-first within tier, tier-weighted across tiers

## Problem

The current hybrid selector (waste-pressure / required-rate) can pick a later-reset account within the same plan-type
tier when it has more remaining secondary credits, which is unintuitive for operators who expect "earliest reset first"
behavior within a tier.

Additionally, cross-tier selection needs to prefer higher tiers (latency and response quality) without introducing
complex per-request cost prediction.

## Goals

- Within the same tier, selection is deterministic and intuitive: **earliest secondary reset wins** (strict ERF).
- Across tiers, selection prefers higher tiers, but allows lower tiers when they reset materially sooner.
- Keep selection explainable and low-complexity; do not use token/cost estimation.
- Preserve pinned-pool and sticky-session semantics.

## Non-goals

- No proactive sticky-session migration on reset boundaries.
- No new dashboard toggles for selection strategy.
- No changes to persistence models for account status / blocked-until hints.

