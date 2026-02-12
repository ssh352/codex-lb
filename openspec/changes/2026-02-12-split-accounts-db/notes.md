## Ops notes

- If `CODEX_LB_ACCOUNTS_DATABASE_URL` points to an iCloud-synced path, avoid running multiple
  instances concurrently against the same accounts DB.
- Keep `encryption.key` with the accounts DB if the goal is to roam authenticated accounts across
  machines.

