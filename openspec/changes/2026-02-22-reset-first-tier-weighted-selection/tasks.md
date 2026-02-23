# Tasks

- [x] Update `openspec/specs/routing-pool/spec.md` and `context.md` to match reset-first ranking and new weights.
- [x] Update `app/core/balancer/logic.py` selector to use per-account `tier_weight / time_to_secondary_reset`.
- [x] Update unit tests in `tests/unit/test_load_balancer.py` for the new selection behavior.
- [x] Run focused unit tests for selection and eligibility gating.
