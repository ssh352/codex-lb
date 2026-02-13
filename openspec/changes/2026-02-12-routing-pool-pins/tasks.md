# Tasks

- [x] Add `pinned_account_ids_json` to `DashboardSettings` and a migration to add the column.
- [x] Expose routing pool via `GET/PUT /api/settings` as `pinnedAccountIds`.
- [x] Add `pinned: bool` to account summary payloads used by dashboard/accounts endpoints.
- [x] Enforce routing pool in proxy `LoadBalancer.select_account`, with fallback to normal selection when unusable.
- [x] Invalidate proxy routing snapshot when settings are updated.
- [x] Add dashboard UI multi-select + bulk actions (Alpine.js) for routing pool + pause/resume/delete.
- [x] Add tests covering routing pool selection + API exposure.

