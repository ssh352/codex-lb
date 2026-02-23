# Proposal

Fix a proxy/load-balancer persistence bug where an in-memory `runtime.reset_at` value can override a later persisted
`accounts.reset_at`, causing accounts to flip back to **Active** (and/or revert to an earlier reset time) even after an
operator or reconciliation job sets a correct future reset boundary in `accounts.db`.

This most commonly shows up after `usage_limit_reached`: the proxy may persist a short reset hint, but operators then
set a longer “blocked until” timestamp (e.g. weekly reset / upstream `resets_at`). The next snapshot rebuild can ignore
the newer persisted value due to runtime precedence, leading to misleading dashboard state and unexpected routing.

