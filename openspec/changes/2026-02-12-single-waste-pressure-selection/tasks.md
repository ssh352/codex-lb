# Tasks

- [ ] Remove `CODEX_LB_PROXY_SELECTION_STRATEGY` from configuration and `.env.example`.
- [ ] Simplify balancer selection to a single waste-pressure algorithm.
- [ ] Adjust waste-pressure scoring for missing secondary usage/reset data per design.
- [ ] Remove/reset-bucket UI toggle from settings API and dashboard UI; update tests accordingly.
- [ ] Update/replace selection strategy tests to cover:
  - known secondary reset beats unknown secondary reset
  - unknown secondary usage does not look like 0%
  - stable tie-breaking remains deterministic

