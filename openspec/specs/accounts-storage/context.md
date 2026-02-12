## Accounts Storage (Split DB): Context

### Purpose

`accounts.db` holds low-churn account credentials (encrypted OAuth tokens). `store.db` holds high-churn operational data (usage history, request logs, sticky sessions, dashboard settings).

Splitting these concerns enables:

- Backing up/roaming authenticated accounts without syncing append-heavy operational tables.
- Simpler operational maintenance for `store.db` (retention, compaction, etc.) without touching account credentials.

### Roaming vs concurrent use

There are two distinct multi-machine goals:

1) **Roaming**: authenticate on machine A, then later use the same accounts on machine B (not at the same time).
2) **Concurrent use**: actively use Codex from multiple machines at the same time against the same account pool.

Roaming can be done by storing `accounts.db` and `encryption.key` on a synced path (e.g. iCloud Drive) and ensuring only one `codex-lb` instance uses the DB at a time.

Concurrent use is best handled by running a single `codex-lb` instance as the authority and having other machines connect to it (see below). This avoids OAuth refresh-token rotation races.

### Failure mode: refresh-token rotation races

OpenAI refresh tokens are typically rotated/invalidated on refresh. If two independent `codex-lb` instances attempt to refresh the same account (even infrequently), one instance can end up using a stale refresh token and trigger errors like `refresh_token_reused`, forcing re-authentication.

### Recommended topology for simultaneous multi-machine use

Run one `codex-lb` instance as the single writer (authority), and connect from multiple machines:

- Authority machine: runs the proxy/dashboard and owns the token refresh process.
- Client machines: forward ports to the authority (or connect over a secure network path) and keep local Codex/OpenCode configs unchanged.

Example (SSH tunnel; keeps client configs pointing at `http://127.0.0.1:2455`):

- Authority: run `codex-lb` bound to loopback (`--host 127.0.0.1 --port 2455`).
- Client(s): `ssh -N -L 2455:127.0.0.1:2455 -L 1455:127.0.0.1:1455 <user>@<authority-host>`
  - Port `1455` is needed only for the OAuth “Add account” flow from the client machine.

### SQLite notes

For SQLite, WAL mode uses `-wal`/`-shm` sidecar files. File-sync tools are not a concurrency-safe database transport for these sidecar files. For this reason, the accounts DB uses rollback journaling (`DELETE`) to reduce file-sync hazards when roaming credentials.

This does not mean WAL is inherently unsafe for a local, single-machine deployment. The hazard is
specifically when the SQLite database is placed on a synced path (or other non-transactional file
transport) where sidecar files can desynchronize. `accounts.db` is low-churn and remains configured
for rollback journaling even in the recommended “single authority instance” topology.

For in-memory SQLite (`:memory:`), WAL mode is not supported; journaling mode requirements apply only to file-backed SQLite databases.
