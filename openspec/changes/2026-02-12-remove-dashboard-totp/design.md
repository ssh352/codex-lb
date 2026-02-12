# Design

## API

- Remove `/api/dashboard-auth/**` endpoints.
- Update `/api/settings`:
  - Response: remove `totpRequiredOnLogin` and `totpConfigured`.
  - Update request: remove `totpRequiredOnLogin`.

## Middleware

- Remove the dashboard TOTP middleware that blocks `/api/*` requests when enforcement is enabled.

## Data model / DB

- Keep the existing `dashboard_settings.totp_*` columns and the migration that introduced them in
  place. The feature is removed at the application layer, but schema cleanup is intentionally
  deferred because dropping columns is not portable (notably on SQLite, it typically requires table
  rebuilds) and would add unnecessary migration churn.

## Frontend

- Remove "TOTP access control" from the Settings view.
- Remove any client-side TOTP prompts and related API calls.

## Tests

- Remove TOTP-specific tests.
- Update settings tests to match the new settings API contract.
