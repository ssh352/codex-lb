# Tasks

- [x] Ensure expired `runtime.reset_at` does not override persisted `accounts.reset_at` when building load-balancer state.
- [x] When both runtime + persisted reset hints exist, use the later boundary (`max`).
- [x] Add unit tests to lock in precedence behavior.

