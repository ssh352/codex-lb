# Tasks

- [x] Remove dashboard TOTP middleware and wiring.
- [x] Remove `dashboard_auth` module and request context wiring.
- [x] Remove TOTP fields from settings schemas/service/repository and update `/api/settings` contract.
- [x] Update dashboard static assets (`app/static/index.html`, `app/static/index.js`) to remove TOTP UI and flows.
- [x] Update tests for removed endpoints and settings contract.
- [x] Remove dashboard TOTP setup references from configuration docs (`README.md`, `.env.example`).
