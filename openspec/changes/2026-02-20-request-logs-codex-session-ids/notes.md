# Notes

- Storing raw identifiers is a deliberate choice for personal, single-user setups: it keeps local
  debugging simple (`WHERE codex_session_id = ?`), and avoids having to re-derive an HMAC key when
  querying historical data.
- Avoid logging these raw identifiers by default to reduce accidental exposure if logs are shared.

