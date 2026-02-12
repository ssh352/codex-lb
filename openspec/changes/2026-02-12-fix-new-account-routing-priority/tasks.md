# Tasks

- [x] Update `app/core/balancer/logic.py` reset-first sort key to treat `secondary_reset_at is None`
      as highest priority (bucket `0`) when `prefer_earlier_reset_accounts=True`.
- [x] Update/add unit tests under `tests/unit` to cover new-account priority.
- [x] Add a short code comment explaining why `None` maps to bucket `0` (new accounts should not be
      permanently deprioritized).
