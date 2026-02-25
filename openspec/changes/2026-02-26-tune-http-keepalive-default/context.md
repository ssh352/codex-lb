# Context

## What this setting does

`CODEX_LB_HTTP_CLIENT_KEEPALIVE_TIMEOUT_SECONDS` maps to `aiohttp.TCPConnector(keepalive_timeout=...)`
and controls how long aiohttp keeps *idle* TCP connections in its pool before closing them.

It does **not** limit the lifetime of an active streaming (SSE) response.

## Why the default is 10 seconds

With some proxy chains, idle pooled connections can be dropped without the client being notified.
Reusing those stale sockets tends to produce `ECONNRESET` right at request start. A lower idle
keep-alive timeout reduces the reuse window and typically improves stability at the cost of more
TCP/TLS handshakes.

## Tuning notes

- If resets happen frequently right at request start, try `5`.
- If the environment is stable and you want fewer handshakes, try `15–30`.
- If you see resets mid-stream, focus on request concurrency and per-host connector limits instead
  of keep-alive.

