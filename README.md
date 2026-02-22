<!--
About
Codex/ChatGPT account load balancer & proxy with usage tracking, dashboard, and OpenCode-compatible endpoints

Topics
python oauth sqlalchemy dashboard load-balancer openai rate-limit api-proxy codex fastapi usage-tracking chatgpt opencode

Resources
-->

# codex-lb

Load balancer for ChatGPT accounts. Pool multiple accounts, track usage, view everything in a dashboard.

## Screenshots

### Main Dashboard View

![main dashboard view](docs/screenshots/dashboard.jpg)

### Accounts View

![Accounts list and details](docs/screenshots/accounts.jpg)

## Quick Start

### Docker

```bash
docker run -d --name codex-lb \
  -p 2455:2455 -p 1455:1455 \
  -v ~/.codex-lb:/var/lib/codex-lb \
  ghcr.io/soju06/codex-lb:latest
```

If you expect high traffic (60+ requests/min), prefer a Docker volume for the data
directory to reduce SQLite I/O errors on some hosts.

```bash
docker volume create codex-lb-data
docker run -d --name codex-lb \
  -p 2455:2455 -p 1455:1455 \
  -v codex-lb-data:/var/lib/codex-lb \
  ghcr.io/soju06/codex-lb:latest
```

### uvx

```bash
uvx codex-lb
```

Open [localhost:2455](http://localhost:2455) → Add account → Done.

## Codex CLI & Extension Setup

Add to `~/.codex/config.toml`:

```toml
model = "gpt-5.2-codex"
model_reasoning_effort = "xhigh"
model_provider = "codex-lb"

[model_providers.codex-lb]
name = "OpenAI"  # MUST be "OpenAI" - enables /compact endpoint
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
chatgpt_base_url = "http://127.0.0.1:2455"
requires_openai_auth = true  # Required: enables model selection in Codex IDE extension
```

## OpenCode Setup

Run:

```bash
opencode auth login
```

Then select `OpenAI` -> `Manually enter API Key` and enter any value.

Add the following to `~/.config/opencode/opencode.json`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "codex-lb": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "codex-lb",
      "options": {
        "baseURL": "http://127.0.0.1:2455/v1"
      },
      "models": {
        "gpt-5.2-codex": { "name": "GPT-5.2 Codex", "reasoning": true, "interleaved": { "field": "reasoning_details" } }
      }
    }
  },
  "model": "codex-lb/gpt-5.2-codex"
}
```

This keeps OpenCode's default providers/connections available and adds `codex-lb` as an extra selectable provider.

If you use `enabled_providers`, include every provider you want to keep plus `codex-lb`; otherwise non-listed providers are hidden.

## Configuration

All settings use the `CODEX_LB_` prefix and can be set via environment variables or `.env.local`.

## Troubleshooting

### Seeing which account Codex is using (and why it errored)

Codex may display a generic message for upstream failures (e.g. “usage limit reached”). To see the *actual* account
used and the raw upstream error text:

1. Open the dashboard: `http://127.0.0.1:2455/dashboard`
2. In **Recent requests**, check:
   - **Account**: the selected account email
   - **Error**: the saved upstream error message (hover / click **More** to expand)

If you need selection history independent of request logs, enable debug endpoints and query the LB event buffer:

- Set `CODEX_LB_DEBUG_ENDPOINTS_ENABLED=1` and restart `codex-lb`
- `GET /debug/lb/events?limit=50` (newest first)
- `GET /debug/lb/state` (current snapshot + eligibility)
  - Note: when disabled (default), these endpoints return 404.

### Debugging missing `prompt_cache_key` / stickiness issues

Enable request logging on the proxy:

- `CODEX_LB_LOG_PROXY_REQUEST_SHAPE=1`: logs request shape (model, stream/compact, input summary, extra keys, and a hashed `prompt_cache_key`).
- `CODEX_LB_LOG_PROXY_REQUEST_SHAPE_RAW_CACHE_KEY=1`: additionally logs a truncated raw `prompt_cache_key` (use with care).
- `CODEX_LB_LOG_PROXY_REQUEST_PAYLOAD=1`: logs the full JSON payload (likely contains sensitive prompt data; use with care).
- `CODEX_LB_REQUEST_LOGS_PROMPT_CACHE_KEY_HASH_ENABLED=1`: persists a short HMAC fingerprint of `prompt_cache_key` to SQLite `request_logs.prompt_cache_key_hash` for postmortem correlation (disabled by default; enable in `.env.local` when needed).

## Data

Data storage locations:

- Local/uvx: `~/.codex-lb/`
- Docker: `/var/lib/codex-lb/`

Files:

- `accounts.db` – accounts (encrypted tokens)
- `store.db` – usage history, request logs, settings
- `encryption.key` – encrypts tokens (auto-generated)

To preserve authenticated accounts across machines, back up `accounts.db` and `encryption.key` (and optionally `store.db` if you want usage history/logs).

### Using Codex from multiple machines at the same time (recommended)

If you need simultaneous use from multiple machines, run a single `codex-lb` instance as the
authority and have other machines connect to it. This avoids refresh-token rotation races because
only the authority instance refreshes and persists tokens.

In this topology, only the authority machine writes account tokens. `accounts.db` is still configured
to use rollback journaling (`DELETE`) (no `-wal`/`-shm` sidecar files) because it also supports the
separate “roaming via iCloud/Dropbox” workflow below.

One simple option is SSH port forwarding (no config changes needed if your clients already point at
`http://127.0.0.1:2455`):

1. Start `codex-lb` on the authority machine (bind to loopback):
   - `uvx codex-lb --host 127.0.0.1 --port 2455`
2. On each client machine, forward the proxy port (and the OAuth callback port for adding accounts):
   - `ssh -N -L 2455:127.0.0.1:2455 -L 1455:127.0.0.1:1455 <user>@<authority-host>`
   - What this does:
     - `-N` tells SSH not to run a remote command (just keep the tunnel open).
     - Each `-L <local_port>:<remote_host>:<remote_port>` forwards a local port on the client to a host/port *reachable from the authority*:
       - `-L 2455:127.0.0.1:2455` makes the client’s `http://127.0.0.1:2455` reach the authority’s `127.0.0.1:2455` (the `codex-lb` proxy).
       - `-L 1455:127.0.0.1:1455` makes the client’s `127.0.0.1:1455` reach the authority’s `127.0.0.1:1455` (OAuth callback, only needed when adding accounts from the client).
     - Replace `<user>` with your SSH username on the authority machine, and `<authority-host>` with its hostname/IP.
3. On the client, open `http://127.0.0.1:2455/dashboard` and use Codex normally.

Notes:

- Don’t run a local `codex-lb` on the client while the tunnel is active (ports 2455/1455 must be free).
- The forwarded `1455` is required only for the OAuth “Add account” flow from the client machine.

### Roaming `accounts.db` via iCloud/Dropbox (not for concurrent use)

If you put `accounts.db` on a synced path to “roam” accounts between machines, treat it as
best-effort backup/roaming. Do not run multiple `codex-lb` instances against the same synced DB.

SQLite WAL uses `-wal`/`-shm` sidecar files, and file-sync tools are not a concurrency-safe database
transport for those sidecars. For this reason, `accounts.db` uses rollback journaling (`DELETE`) to
reduce file-sync hazards (but syncing can still be delayed/stale; wait for sync to finish before
switching machines).

### Migrating from legacy single-DB installs

If you previously used a single `store.db` that contained `accounts`, you can copy those rows into
`accounts.db`:

```bash
codex-lb migrate-accounts
```

If you are running from a source checkout (without installing the package), invoke the CLI module directly:

```bash
./.venv/bin/python -m app.cli migrate-accounts
```

To also remove the legacy `accounts` table from `store.db` after a successful copy:

```bash
codex-lb migrate-accounts --drop-legacy
```

## Contributors ✨

Thanks goes to these wonderful people ([emoji key](https://allcontributors.org/docs/en/emoji-key)):
<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/Soju06"><img src="https://avatars.githubusercontent.com/u/34199905?v=4?s=100" width="100px;" alt="Soju06"/><br /><sub><b>Soju06</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=Soju06" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/commits?author=Soju06" title="Tests">⚠️</a> <a href="#maintenance-Soju06" title="Maintenance">🚧</a> <a href="#infra-Soju06" title="Infrastructure (Hosting, Build-Tools, etc)">🚇</a></td>
      <td align="center" valign="top" width="14.28%"><a href="http://jonas.kamsker.at/"><img src="https://avatars.githubusercontent.com/u/11245306?v=4?s=100" width="100px;" alt="Jonas Kamsker"/><br /><sub><b>Jonas Kamsker</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=JKamsker" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/issues?q=author%3AJKamsker" title="Bug reports">🐛</a> <a href="#maintenance-JKamsker" title="Maintenance">🚧</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/Quack6765"><img src="https://avatars.githubusercontent.com/u/5446230?v=4?s=100" width="100px;" alt="Quack"/><br /><sub><b>Quack</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=Quack6765" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/issues?q=author%3AQuack6765" title="Bug reports">🐛</a> <a href="#maintenance-Quack6765" title="Maintenance">🚧</a> <a href="#design-Quack6765" title="Design">🎨</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/hhsw2015"><img src="https://avatars.githubusercontent.com/u/103614420?v=4?s=100" width="100px;" alt="Jill Kok, San Mou"/><br /><sub><b>Jill Kok, San Mou</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=hhsw2015" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/commits?author=hhsw2015" title="Tests">⚠️</a> <a href="#maintenance-hhsw2015" title="Maintenance">🚧</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/pcy06"><img src="https://avatars.githubusercontent.com/u/44970486?v=4?s=100" width="100px;" alt="PARK CHANYOUNG"/><br /><sub><b>PARK CHANYOUNG</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=pcy06" title="Documentation">📖</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/choi138"><img src="https://avatars.githubusercontent.com/u/84369321?v=4?s=100" width="100px;" alt="Choi138"/><br /><sub><b>Choi138</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=choi138" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/issues?q=author%3Achoi138" title="Bug reports">🐛</a> <a href="https://github.com/Soju06/codex-lb/commits?author=choi138" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/dwnmf"><img src="https://avatars.githubusercontent.com/u/56194792?v=4?s=100" width="100px;" alt="LYA⚚CAP⚚OCEAN"/><br /><sub><b>LYA⚚CAP⚚OCEAN</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=dwnmf" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/commits?author=dwnmf" title="Tests">⚠️</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

This project follows the [all-contributors](https://github.com/all-contributors/all-contributors) specification. Contributions of any kind welcome!
