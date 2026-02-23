# Design

## UX model

Introduce a dashboard variable, `accounts_top_n`, used by the leaderboard panels’ PromQL:

- Default: `20`
- “All”: `10000` (large enough to exceed any realistic account count)

This avoids duplicating panels while keeping the existing overview behavior.

For “realtime” charts (e.g. top accounts by cost/hr / requests/min), use a separate variable with a smaller default
to preserve readability and avoid rendering too many time series.

## Semantics

- “All accounts” means “all accounts with non-empty series in the panel’s underlying metric/time window”.
  - Accounts with no activity in the window may still not appear, because no metric series exists for them.

## Dashboard wiring

- Add a `templating.list` entry:
  - `name`: `accounts_top_n`
  - `type`: `custom`
  - options: `20,50,100,200,10000`
  - default: `20`
  - label: `Top N accounts (tables)`
  - description: clarify “10000” means “all accounts with activity”
- Add a `templating.list` entry for realtime charts:
  - `name`: `accounts_top_n_realtime`
  - `type`: `custom`
  - options: `5,10,20,50,100,200`
  - default: `10`
  - label: `Top N accounts (realtime charts)`
  - description: clarify this should stay small for readability/perf
- Update the affected queries:
  - `topk(20, ...)` → `topk($accounts_top_n, ...)`
  - `topk(10, ...)` (realtime charts) → `topk($accounts_top_n_realtime, ...)`
