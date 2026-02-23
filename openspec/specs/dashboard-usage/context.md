## Dashboard Usage: Quota Reset (Context)

### What “Reset in 2d” means

Some dashboard views show a “Reset in …” label for quota resets. This label is based on the
**secondary (7d) usage window** reset timestamp (`resetAt` / `reset_at_secondary`).

Important nuance: the backend derives the secondary summary reset timestamp as the **earliest
reset time across all accounts** that have a known secondary reset. This makes any summary
countdown conservative.

So “Reset in 2d” means:

- At least one account’s 7‑day quota window will reset in ~2 days.
- Other accounts may reset later; check the per-account “Quota reset” values for account-specific
  reset times.

### Rounding behavior

The dashboard displays “in Xm / in Xh / in Xd” (or “in Xd Yh” once the remaining time exceeds
24 hours) using a ceiling rounding strategy. For example, “in 2d” can mean between a bit over
1 day and up to 2 days remaining, depending on the exact timestamp.

### Example

If you have two accounts with secondary reset times:

- Account A resets in 2 days
- Account B resets in 5 days

Then any summary “Reset in …” label will show “Reset in 2d”, while account list/cards will show
each account’s own reset.

### Status vs upstream message

The dashboard can show two related but distinct pieces of information:

- **Upstream message** (in request logs): the raw error code/message returned by the upstream server (e.g.
  `usage_limit_reached` / “The usage limit has been reached”).
- **codex-lb account status** (in account lists/cards): a codex-lb classification (`rate_limited` / `quota_exceeded`)
  based on upstream error signals *and* locally-derived usage meter state (especially the secondary 7d window).

As a result, it is expected that an account can show an upstream “usage limit reached” message in Recent requests
while the account status is **Quota exceeded** due to secondary-window exhaustion.

Important clarification: `usage_limit_reached` is treated as rate-limit-like for account status (it maps to
`rate_limited`, not `quota_exceeded`). The status becomes `quota_exceeded` only with an explicit quota-style error
code and/or confirmed secondary-window exhaustion.

### What “Consumed 77%” means (Remaining quota by account)

Some dashboard views show a “Remaining quota by account (7D)” visualization with a “Consumed” legend entry.

“Consumed 77%” means: across all accounts, **77% of the total secondary (7d) capacity (credits) is already used**
right now (credit-weighted by plan capacity), so ~23% remains.

Important nuance: accounts can have different secondary reset timestamps. The consumed/remaining percentages are a
point-in-time aggregate across accounts’ *current* secondary windows — they do **not** imply all accounts share one
common “week” or reset moment.

### Stale blocked statuses (“Retry at now”)

Some UI surfaces show a “Retry at …” timestamp based on `statusResetAt`. This is distinct from quota-reset
timestamps and is intended to answer: “when will codex-lb try routing to this account again?”

If an account is persisted as blocked (`rate_limited` / `quota_exceeded`) but its effective `statusResetAt` boundary is
already in the past, the UI can end up showing a retry boundary of “now” indefinitely unless persisted state is
reconciled.

To avoid this confusing operator experience, dashboard APIs reconcile persisted blocked states when the effective
boundary is `<= now`, and return the account as `active` with `statusResetAt = null`.
