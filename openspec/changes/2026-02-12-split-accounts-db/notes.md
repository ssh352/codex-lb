## Ops notes

### Goal: roam accounts across machines

If you want to authenticate on one machine and use the same accounts on another machine, store
`accounts.db` and `encryption.key` on a synced path and point both machines at the same files.

### Constraints (important)

- This supports **roaming**, not **concurrent use**.
- Do not run multiple `codex-lb` instances against the same synced `accounts.db`.
  - OAuth refresh tokens are rotated/invalidated on refresh; two machines can race and trigger
    `refresh_token_reused` / “re-login required”.
- File-sync tools are not a concurrency-safe database transport. To reduce WAL/SHM desync risk,
  `accounts.db` uses rollback journaling (no WAL/SHM sidecar files), but sync delay can still
  cause stale reads if you switch machines too quickly.
  - Switching journal modes may require an exclusive lock; stop all `codex-lb` instances once after
    upgrading so the accounts DB can settle into rollback journaling.

### Recommended workflow

- Stop `codex-lb` on machine A.
- Wait for iCloud Drive to finish syncing `accounts.db` and `encryption.key`.
- Start `codex-lb` on machine B and use the account(s).

### Simultaneous multi-machine use (recommended)

If you need to use Codex from multiple machines at the same time, use a single `codex-lb` instance
as the authority (single writer) and have other machines connect to it (e.g. via SSH tunnels).
This avoids refresh-token rotation races entirely because only the authority instance refreshes and
persists tokens.

### Example configuration

```bash
# Operational data (high churn, per-machine)
CODEX_LB_DATABASE_URL=sqlite+aiosqlite:///~/.codex-lb/store.db

# Accounts (low churn, roamed via iCloud)
CODEX_LB_ACCOUNTS_DATABASE_URL="sqlite+aiosqlite:////Users/<you>/Library/Mobile Documents/com~apple~CloudDocs/dotfiles/codex-lb/accounts.db"
CODEX_LB_ENCRYPTION_KEY_FILE="/Users/<you>/Library/Mobile Documents/com~apple~CloudDocs/dotfiles/codex-lb/encryption.key"
```
