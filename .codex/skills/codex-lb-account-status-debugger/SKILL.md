---
name: codex-lb-account-status-debugger
description: Debug codex-lb account status end-to-end (accounts.db + store.db + dashboard APIs) to explain why an account shows active/paused/rate_limited/quota_exceeded/deactivated, and to troubleshoot mismatches between the Accounts page and actual upstream behavior (e.g., usage_limit_reached 429s). Use when asked to “probe an account”, “why is it active”, “is it rate limited”, or to update/verify an account’s status/reset time.
---

# codex-lb account status debugger

## Workflow

1) Look up the account row in `~/.codex-lb/accounts.db`.
2) Inspect recent `request_logs` and `usage_history` in `~/.codex-lb/store.db`.
3) Compare with what the dashboard consumes:
   - `GET /api/accounts`
   - `GET /api/dashboard/overview`
4) (Optional) If you need ground truth, run a forced live probe request through the proxy for that exact account id.

## Run the helper script

- DB + API report (no upstream request):
  - `python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-lb-account-status-debugger/scripts/account_status_debug.py" --email <email>`
- Include a forced live probe (may mark the account rate-limited if upstream returns 429):
  - `python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-lb-account-status-debugger/scripts/account_status_debug.py" --email <email> --probe`
- Probe *all* currently rate-limited accounts (DB status = `rate_limited`):
  - `python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-lb-account-status-debugger/scripts/account_status_debug.py" --all-rate-limited --probe`
- Probe *all* accounts in a given status:
  - `python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-lb-account-status-debugger/scripts/account_status_debug.py" --all-status quota_exceeded --probe`
- If `/api/dashboard/overview` is slow/timeouts, skip it:
  - `python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-lb-account-status-debugger/scripts/account_status_debug.py" --email <email> --skip-overview`
- Generate a message draft you can forward to the account owner (does not send):
  - `python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-lb-account-status-debugger/scripts/account_status_debug.py" --email <email> --message-draft`
- Machine-readable output:
  - `python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-lb-account-status-debugger/scripts/account_status_debug.py" --email <email> --format json`
- Redact PII (email/account_id) in output:
  - `python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-lb-account-status-debugger/scripts/account_status_debug.py" --email <email> --redact`
- Dry-run a DB correction (single account only; requires a live probe):
  - `python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-lb-account-status-debugger/scripts/account_status_debug.py" --email <email> --probe --apply-dry-run`
- Apply a DB correction (writes `accounts.db`; single account only; requires a live probe):
  - `python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-lb-account-status-debugger/scripts/account_status_debug.py" --email <email> --probe --apply`

If the script isn't found, confirm `CODEX_HOME` points at your Codex home (the dir that contains `skills/`):
- `echo "${CODEX_HOME:-$HOME/.codex}" && ls -la "${CODEX_HOME:-$HOME/.codex}/skills/codex-lb-account-status-debugger/scripts" | rg account_status_debug`

## Notes / guardrails

- Do not print or log decrypted tokens.
- Prefer concrete timestamps: show `reset_at` both as epoch seconds and as ISO 8601 UTC.
- If the APIs disagree with the DB, suspect caching or multiple running server instances; include:
  - `lsof -nP -iTCP:2455 -sTCP:LISTEN`
  - which base URL the UI is hitting (usually `http://127.0.0.1:2455`).
