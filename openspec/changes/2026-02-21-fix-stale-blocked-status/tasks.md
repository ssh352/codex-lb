# Tasks

- [x] Dashboard overview clears stale blocked statuses (already implemented in `app/modules/dashboard/service.py` using `stale_blocked_account_ids`).
- [x] `GET /api/accounts` clears stale blocked statuses (implemented in `app/modules/accounts/service.py` using `stale_blocked_account_ids`).
- [x] Confirm cache behavior is acceptable when reconciliation writes changes (accounts list cache TTL is short and reconciliation runs before response caching).
- [x] Keep `AccountsRepository.bulk_update_status_fields()` for reconciliation (simplicity; list sizes are small).
- [x] Add integration tests covering:
  - `GET /api/accounts` returns cleared status when effective reset boundary is `<= now` (including cases where the only known boundary comes from usage reset timestamps).
  - blocked state is NOT cleared when the effective reset boundary is in the future.
  - blocked state is NOT cleared when the effective reset boundary is unknown (null).
- [x] Update OpenSpec documentation to define expected convergence behavior for persisted blocked statuses:
  - requirements: `openspec/specs/dashboard-account-status/spec.md`
  - context: `openspec/specs/dashboard-account-status/context.md`
