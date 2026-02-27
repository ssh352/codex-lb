# Responses API Compatibility Context

## Purpose and Scope

This capability implements OpenAI-compatible behavior for `POST /v1/responses`, including request validation, streaming events, non-streaming aggregation, and OpenAI-style error envelopes. The scope is limited to what the ChatGPT upstream can provide; unsupported features are explicitly rejected.

See `openspec/specs/responses-api-compat/spec.md` for normative requirements.

## Rationale and Decisions

- **Responses as canonical wire format:** Internally we treat Responses as the source of truth to avoid divergent streaming semantics.
- **Strict validation:** Required fields and mutually exclusive fields are enforced up front to match official client expectations.
- **No truncation support:** Requests that include `truncation` are rejected because upstream does not support it.

## Constraints

- Upstream limitations determine available modalities, tool output, and overflow handling.
- `store=true` is rejected; responses are not persisted.
- `include` values must be on the documented allowlist.
- `previous_response_id` and `truncation` are rejected.
- `/v1/responses/compact` is supported only when the upstream implements it.

## Include Allowlist (Reference)

- `code_interpreter_call.outputs`
- `computer_call_output.output.image_url`
- `file_search_call.results`
- `message.input_image.image_url`
- `message.output_text.logprobs`
- `reasoning.encrypted_content`
- `web_search_call.action.sources`

## Failure Modes

- **Stream ends without terminal event:** Emit `response.failed` with `stream_incomplete`.
- **Upstream error / no accounts:** Non-streaming responses return an OpenAI error envelope with 5xx status.
- **Invalid request payloads:** Return 4xx with `invalid_request_error`.

## Error Envelope Mapping (Reference)

- 401 → `invalid_api_key`
- 403 → `insufficient_permissions`
- 404 → `not_found`
- 429 → `rate_limit_exceeded`
- 5xx → `server_error`

## Examples

Non-streaming request/response:

```json
// request
{ "model": "gpt-5.1", "input": "hi" }
```

```json
// response
{ "id": "resp_123", "object": "response", "status": "completed", "output": [] }
```

## Operational Notes

- Pre-release: run unit/integration tests and optional OpenAI client compatibility tests.
- Smoke tests: stream a response, validate non-stream responses, and verify error envelopes.
- Post-deploy: monitor `no_accounts`, `stream_incomplete`, and `upstream_unavailable`.
- Quick triage for `upstream_unavailable`:
  - Messages like `RECORD_LAYER_FAILURE`, `Connection reset by peer`, or `Server disconnected`
    are usually transport path issues (proxy/VPN/NAT/network) rather than account auth problems.
  - Switching/re-importing ChatGPT login usually does not resolve those transport errors.
  - Login/account actions are mainly for auth/quota failures (for example `401`/`invalid_api_key`,
    `invalid_auth`, `auth_refresh_failed`, `usage_limit_reached`).
- If you see `502` with `upstream_unavailable` and a message like `Timeout on reading data from socket` on `/responses/compact`, increase the upstream compact timeout:
  - `CODEX_LB_UPSTREAM_COMPACT_TIMEOUT_SECONDS` (default `300`)
- Read-timeout on `/responses/compact` means: the proxy connected and sent the request upstream, but did not
  receive response bytes within the configured socket read timeout. This can be normal for slow non-streaming
  upstream work (the upstream may not send any bytes until it finishes).
- Where `502` comes from: `aiohttp` socket read timeouts are raised as `aiohttp.ClientError`, mapped to
  an OpenAI error envelope with HTTP 502 (`upstream_unavailable`) in `app/core/clients/proxy.py`, then returned
  by the FastAPI handler in `app/modules/proxy/api.py`.
- Tradeoffs: increasing the compact timeout reduces spurious 502s for slow upstream requests, but also keeps
  in-flight requests open longer (more sockets/memory), which can worsen overload behavior under concurrency.
  If timeouts happen only under load, also consider tuning client limits:
  - `CODEX_LB_HTTP_CLIENT_CONNECTOR_LIMIT`
  - `CODEX_LB_HTTP_CLIENT_CONNECTOR_LIMIT_PER_HOST`
- To debug client routing/stickiness metadata, enable proxy logging:
  - `CODEX_LB_LOG_PROXY_REQUEST_SHAPE=1` (includes a hashed `prompt_cache_key`)
  - `CODEX_LB_LOG_PROXY_REQUEST_SHAPE_RAW_CACHE_KEY=1` (adds a truncated raw `prompt_cache_key`; use with care)
  - `CODEX_LB_LOG_PROXY_REQUEST_PAYLOAD=1` (logs full request JSON; likely contains sensitive prompt data)
  - `CODEX_LB_REQUEST_LOGS_PROMPT_CACHE_KEY_HASH_ENABLED=1` (persists an HMAC fingerprint to SQLite `request_logs`)

### Postmortem stickiness debugging (SQLite)

If you need to answer questions after the fact (even when terminal logs were not retained), enable:

- `CODEX_LB_REQUEST_LOGS_PROMPT_CACHE_KEY_HASH_ENABLED=1`

This writes a short HMAC fingerprint (`hmac_sha256:...`) of the request `prompt_cache_key` into the main operational
SQLite database (`~/.codex-lb/store.db`) in the `request_logs.prompt_cache_key_hash` column.

This is disabled by default to keep the default installation low-noise and low-retention: it adds per-request metadata
to the DB that is primarily useful for debugging and postmortems. Enable it explicitly (e.g. in `.env.local`) when you
need this correlation.

Example query:

```sql
SELECT requested_at, account_id, request_id, prompt_cache_key_hash
FROM request_logs
WHERE prompt_cache_key_hash = 'hmac_sha256:deadbeefcafe'
ORDER BY requested_at DESC
LIMIT 50;
```
