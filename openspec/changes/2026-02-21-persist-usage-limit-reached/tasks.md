# Tasks

- [ ] Update `handle_usage_limit_reached()` to persist `rate_limited` when upstream provides a concrete reset time
      and the delay-to-reset exceeds the chosen threshold.
- [ ] Add unit tests for:
  - [ ] With `resets_at` (long delay) => status becomes `rate_limited` and `reset_at` is set
  - [ ] With no reset hints => status remains `active` and uses backoff cooldown
  - [ ] With reset hint but short delay (< threshold) => remains `active` (soft cooldown only)
- [ ] Verify persisted limit survives restart (account not selected until `reset_at`).
- [ ] Verify dashboard shows `rate_limited` (not `active`) during the lockout period.

