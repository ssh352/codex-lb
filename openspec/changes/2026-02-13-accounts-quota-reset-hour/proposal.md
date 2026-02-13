# Proposal: Show hours in per-account quota reset labels

## Problem

The Accounts table "Quota reset (7D)" column currently displays relative values as `in Xd` once the reset is more
than 24 hours away. This is too coarse for operators when comparing account resets.

## Goals

- When a quota reset is more than 24 hours away, display remaining time as `in Xd Yh`.
- Keep the existing ceiling rounding strategy (conservative countdown).

## Non-goals

- Changing any backend quota reset derivation or API contracts.
- Replacing the dashboard frontend stack.

