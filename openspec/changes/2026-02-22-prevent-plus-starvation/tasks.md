# Tasks

- [ ] Fix `app/core/config/settings.py` indentation regression (stray tab-indented fields).
- [ ] Update balancer tier scoring to prevent tier-size starvation (max-urgency aggregation).
- [ ] Make secondary-exhausted accounts ineligible for routing until reset.
- [ ] Update selection trace/debug surfaces to reflect new aggregation mode.
- [ ] Update `openspec/specs/routing-pool/spec.md` + `context.md` to match the new hybrid selector.
- [ ] Add/adjust unit tests for cross-tier starvation prevention and exhausted-secondary ineligibility.
- [ ] Run focused unit/integration tests for selection + stickiness behavior.
