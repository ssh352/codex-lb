---
name: codex-lb-reset-cycle-report
description: Manually calculate and report a codex-lb account's usage for the current and/or previous reset cycle from local ~/.codex-lb SQLite databases (accounts.db and store.db). Use for questions like "how much USD consumed in the last reset cycle", "how many requests", "cost per request", and "did usage_percent jump without codex-lb requests (outside usage)?".
---

# Codex LB Reset Cycle Report

Generate a reproducible, manual report for an account’s reset-cycle usage and estimated USD cost using local codex-lb logs.

## Quick start

From the `codex-lb` repo root:

```bash
.venv/bin/python .claude/skills/codex-lb-reset-cycle-report/scripts/codex_lb_reset_cycle_report.py \
  --email you@example.com
```

## Notes

- Cycle bounds come from `store.db.usage_history` (`reset_at` + `window_minutes`).
- Requests/tokens come from `store.db.request_logs` inside the computed time bounds.
- USD uses the repo’s pricing logic in `app/core/usage/pricing.py` (estimated; not “billed”).
