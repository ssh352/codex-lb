# Design

## UX model

Replace the unreadable “top 20” charts with two complementary rank views per metric:

1) **Rank (yesterday):** a 2-column **Table** for the **last completed day** (“yesterday” in browser timezone).
2) **Rank (30d):** a 2-column **Table** showing **30d total**, as a longer-horizon complement.

Each table row is a single account, with:

- **Account (left):** email text colored by account `plan_type` (free/plus/pro/unknown).
- **Value (right):** a gauge cell using value-based gradient coloring (same as the “Quota (USD, 7d)” column in the
  “Estimated Weekly Quota (USD, 7d)” panel).

Reason: Grafana’s bar gauge styling can’t reliably color only the label text independently of the bar fill, so the
table+gauge pattern is used.

Both views are provided for:

- cost/day (USD)
- requests/day

## Semantics (SSOT)

- **Yesterday**: the last completed day, defined by `timeFrom: now-2d/d` and `timeTo: now/d` with a `[1d]` window at
  `interval: 1d`. This matches the daily-rollup panels whose rightmost bar is typically yesterday.
- **30d total**: show totals over `now-30d/d → now/d` (completed days only).

## PromQL

### Identity join (existing pattern)

Use the same identity join as elsewhere in the dashboard to attach `display` + `plan_type` while preserving
`account_id`:

- `codex_lb_account_identity`
- fallback: `label_replace(count by (account_id) (codex_lb_secondary_used_percent), "display", "$1", "account_id", "(.*)")`

Do **not** overwrite `account_id`.

To avoid scaling the left-hand metric values by the identity metric value, join against a “ones” vector that preserves
labels:

- `0 * (identity ...) + 1`

Then build a single display label:

- `label_join(..., "account_display", "::", "plan_type", "display")`

### Rank: “yesterday top 20”

- Cost:
  - `topk(20, sum by (account_id) (increase(codex_lb_proxy_account_cost_usd_total{...}[1d])))` joined to identity for
    `display` + `plan_type`, then `label_join`’d into `account_display`.
- Requests:
  - `topk(20, sum by (account_id) (increase(codex_lb_proxy_account_requests_total{...}[1d])))` joined to identity for
    `display` + `plan_type`, then `label_join`’d into `account_display`.

### Rank: “30d total top 20”

- Cost:
  - `topk(20, sum by (account_id) (increase(codex_lb_proxy_account_cost_usd_total{...}[30d])))` joined to identity for
    `display` + `plan_type`, then `label_join`’d into `account_display`.
- Requests:
  - `topk(20, sum by (account_id) (increase(codex_lb_proxy_account_requests_total{...}[30d])))` joined to identity for
    `display` + `plan_type`, then `label_join`’d into `account_display`.

## Drilldown

Optional follow-up: add a data link from the Account cell to filter `$account` to the row’s `account_id`.

## Layout

- Enlarge the “top 20” rank panels so 20 rows are legible.
- Insert the 30d total rank panels above the “Today so far” row; shift panels below accordingly.
