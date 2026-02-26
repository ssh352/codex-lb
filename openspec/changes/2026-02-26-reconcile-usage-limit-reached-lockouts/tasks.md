# Tasks

- [ ] Add settings:
  - [x] `CODEX_LB_USAGE_LIMIT_REACHED_MIN_COOLDOWN_SECONDS`
  - [x] `CODEX_LB_USAGE_LIMIT_REACHED_MAX_INITIAL_COOLDOWN_SECONDS`
  - [x] `CODEX_LB_USAGE_LIMIT_REACHED_ESCALATE_STREAK_THRESHOLD`
  - [x] `CODEX_LB_USAGE_LIMIT_REACHED_PERSIST_RESET_THRESHOLD_SECONDS`

- [x] Implement dedicated `usage_limit_reached` handler in the balancer logic:
  - [x] Enforce min cooldown when no reset hint exists.
  - [x] When reset hint exists and is “long”, cap initial cooldown.
  - [x] Escalate to persisting `reset_at` only on repeated-streak evidence (and/or confirmed weekly exhaustion).

- [x] Update proxy error classification path to call the dedicated handler for `usage_limit_reached` (not the generic
      rate limit handler), without changing `quota_exceeded` semantics.

- [x] Unit tests:
  - [x] No reset hint => cooldown is >= min cooldown.
  - [x] Reset hint long + first occurrence => cooldown is capped; `reset_at` not persisted.
  - [x] Reset hint long + repeated streak => `reset_at` persisted to upstream boundary.
  - [x] Secondary usage >= 100 => `quota_exceeded` derived from telemetry still wins.

- [ ] Operator validation queries (post-deploy):
  - [ ] Re-run the “next request within 60s after `usage_limit_reached` ends” query; verify the 67% figure drops
        materially (target: < 20%).
