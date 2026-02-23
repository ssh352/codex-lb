# Design

## Root cause

When building account states for selection, codex-lb combines:

- the durable persisted “blocked until” hint: `accounts.reset_at`, and
- the in-memory “blocked until” hint: `runtime.reset_at` (from recent marks).

If state construction prefers `runtime.reset_at` via a simple truthy check, it can:

- keep using an expired runtime value,
- ignore a later persisted reset boundary set out-of-band (e.g. via reconciliation), and
- cause a subsequent DB write-back that reverts the persisted state.

## Fix strategy

When constructing effective state:

- Treat expired `runtime.reset_at` as stale and clear it.
- If both `runtime.reset_at` and `accounts.reset_at` exist, use the later boundary (`max`) as the effective reset.

This preserves the most conservative “blocked until” boundary without requiring a server restart.

## User-visible impact

- Dashboard/API status stays consistent with the persisted reset boundary when it is later than a prior runtime hint.
- Operator-driven corrections to `accounts.reset_at` are not immediately undone by the next proxy snapshot rebuild.

