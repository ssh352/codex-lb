# Design

## Forced-account routing (debug)

- Introduce an internal request header `x-codex-lb-force-account-id: <account_id>`.
- When present:
  - route the request to that exact `accounts.id` (bypasses pinned pool + normal eligibility checks)
  - disable failover/retry across accounts (`max_attempts=1`)
- The header must never be forwarded to upstream.

Rationale: pinning is intentionally not a hard guarantee; the forced-account header exists for deterministic operator
debugging (“show me what *this* account returns right now”).

## Dashboard surfacing (`statusResetAt`)

Codex-lb’s routing eligibility is not purely derived from usage telemetry window resets; the load balancer can also
persist a temporary "blocked until" boundary in `accounts.reset_at` when it marks an account `rate_limited` /
`quota_exceeded`.

To avoid the "account looks active but isn't selectable" confusion, dashboard APIs expose this persisted boundary as
`statusResetAt` on `AccountSummary` (serialized as an ISO 8601 datetime).

`statusResetAt` is computed as:

- `accounts.reset_at` when present (explicit LB mark / persisted cooldown), otherwise
- the relevant usage window reset timestamp (`usage_history.reset_at`) when the account is blocked due to usage
  telemetry (`rate_limited` → primary reset, `quota_exceeded` → secondary reset).

The dashboard renders:

- On the dashboard account cards: `Blocked · Retry …` in preference to quota reset meta.
- On the Accounts tab: a muted sub-label under the Status pill and a `Blocked until …` field in the Selected account
  panel.

## `usage_limit_reached` lockout policy

When upstream returns `usage_limit_reached`:

- Always mark the account `rate_limited` with a cooldown boundary (fail-open with backoff).
- Cap the *initial* cooldown boundary to a small maximum (default: 5 minutes), even if upstream provides a far
  `resets_at` boundary.
- Escalate to the upstream reset boundary (`resets_at`) only when:
  - repeated `usage_limit_reached` errors occur within the same error streak (`error_count >= 3`), or
  - local telemetry shows the weekly (secondary) window is exhausted (`secondary_used_percent >= 100` and a reset is known)

This keeps the system responsive to transient upstream errors while still converging on a persistent lockout when
evidence accumulates.
