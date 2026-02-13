# Design

## Dashboard UI

- Update the dashboard relative time formatter so that:
  - `< 60 minutes` stays `in Xm`
  - `< 24 hours` stays `in Xh`
  - `>= 24 hours` becomes `in Xd Yh` (omit the `Yh` portion when `Y == 0`)
- Continue using ceiling rounding for minutes/hours so the label remains conservative.

## OpenSpec docs

- Update `openspec/specs/dashboard-usage/context.md` to reflect the `in Xd Yh` formatting.

