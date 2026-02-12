# Notes: Accounts Page Performance Plan (migrated)

This document was originally `account-performance-plan.md` at the repo root.

In this repo, OpenSpec is the SSOT for change-driven work, so the canonical copy now lives here.

---

# Performance Improvement: Account Page Loading Under High Concurrency

## Problem Statement

Under high concurrency (10+ Codex sessions), the account page (`GET /api/accounts`) loads very slowly.

From the current implementation, the biggest cost driver is not “Python vs Rust”, it’s the *shape of the DB work*:
- `GET /api/accounts` runs `list_accounts()` plus **two calls** to `UsageRepository.latest_by_account()` (primary + secondary).
- `latest_by_account()` currently selects **all matching usage history rows**, then keeps the first row per `account_id` in Python.

On the current local DB (`/Users/zhang/.codex-lb/store.db`) this means:
- `accounts`: 33 rows
- `usage_history`: ~90k rows
- `request_logs`: ~48k rows

So today, one `/api/accounts` request can easily pull tens of thousands of rows twice (primary + secondary) just to return ~33 “latest” entries.

SQLite can still be a bottleneck under write-heavy concurrency (single-writer), but even on Postgres the current query pattern will remain expensive.

## Are “rewrite in Rust” or “SQLite → Postgres” necessary?

- **Rewrite in Rust**: Not necessary for the observed bottleneck. After fixing query shape + DB contention, you can profile again; only then decide if Python CPU overhead is still significant.
- **SQLite → Postgres**: Not a low-hanging fruit. It’s a valid long-term move if you need reliable high write throughput / multi-process concurrency, but it’s heavier (ops + migrations + deployment changes). Do it *after* the cheap wins below, unless you already know you must support sustained write-heavy concurrency.

## Re-Ordered Plan (Low Hanging + High Impact → Complex)

**Status (implemented in this repo)**:
- ✅ Phase 0: Baseline scripts (`scripts/perf_accounts.py`, non-prod ports like 2456+)
- ✅ Phase 1: “latest per account” fixed in SQL (`UsageRepository.latest_by_account`)
- ✅ Phase 2: Window-aware index migration for `usage_history`
- ✅ Phase 3: Fewer DB roundtrips for primary+secondary usage (`latest_primary_secondary_by_account`)
- ✅ Phase 4: Short TTL cache for `/api/accounts` (plus invalidation on mutations)
- ✅ Phase 5 (partial): Fewer commits during usage refresh (commit per account)

### Phase 0: Measure Baseline (Easy, High Signal)

- Confirm row counts and validate the “too many rows returned” hypothesis.
- Capture p50/p95/p99 latency for `/api/accounts` on a **non-production port** (e.g. 2456).
  - Script: `scripts/perf_accounts.py`
- Capture DB timings for the three queries used by `/api/accounts` (accounts list + latest primary + latest secondary).

### Phase 1: Stop Fetching All Usage Rows (Highest ROI, Still Easy)

**Task 1.1: Rewrite `latest_by_account()` to return 1 row per account in SQL**
- **File**: `app/modules/usage/repository.py`
- **Change**: Replace “fetch all then filter in Python” with a DB query that returns only the latest row per `account_id`.
- **Impact**: Removes the dominant overhead on `/api/accounts` and any other call sites (dashboard/proxy).
- **Risk**: Low-to-medium (query correctness), but localized and testable.

Implementation options (pick one; both are valid):
- **SQLite-compatible window function**: `row_number() over (partition by account_id order by recorded_at desc)` then `where rn = 1`.
- **Correlated subquery per account** (often fast with a supporting index and small account counts).

Current inefficient approach:
```python
# Fetches ALL rows, filters in Python - O(n) rows
stmt = select(UsageHistory).where(conditions).order_by(...)
for entry in result.scalars().all():
    if entry.account_id not in latest:
        latest[entry.account_id] = entry
```

Optimized approach (conceptually):
```python
# Database returns only 1 row per account - O(accounts) rows returned
```

**Task 1.2: Make “primary” window explicit (remove NULL semantics)**
- **Change**: Stop writing `window=None` for primary; backfill existing `NULL` to `"primary"` in a migration.
- **Impact**: Simplifies query predicates (`WHERE window='primary'`), improves index usability, reduces OR conditions.
- **Risk**: Medium (data migration), but the code already treats `NULL` as primary so it’s a compatibility cleanup.

### Phase 2: Add/Adjust Indexes for the New Query (Easy, Medium ROI)

Note: the DB already has `idx_usage_account_time (account_id, recorded_at)` and `idx_usage_recorded_at (recorded_at)`.

**Task 2.1: Add a window-aware index for “latest per account by window”**
- **Change**: Add a composite index to support `WHERE window=? AND account_id=? ORDER BY recorded_at DESC` patterns.
- **Suggested index**:
  - `CREATE INDEX IF NOT EXISTS idx_usage_window_account_recorded ON usage_history(window, account_id, recorded_at DESC);`
- **Impact**: Helps the DB avoid scanning unrelated windows and speeds up “latest per account” lookups.
- **Risk**: Low (additive).

**Task 2.2: Ensure `accounts.email` is indexed**
- **Change**: Add index on `accounts(email)` if not already present (unique constraint may already create an index depending on migration history).
- **Impact**: Minor, but makes `ORDER BY email` stable under growth.
- **Risk**: Low.

### Phase 3: Reduce DB Roundtrips Per Request (Easy, Medium ROI)

**Task 3.1: Avoid duplicate usage scans**
- **File**: `app/modules/accounts/service.py`
- **Change**: Fetch primary + secondary usage in one repository call (or one SQL query) if practical.
- **Impact**: Less total DB work and fewer awaits per request.
- **Risk**: Low-to-medium (new query shape).

### Phase 4: Add Caching (Easy, High ROI If Read-Heavy)

**Task 4.1: Cache `/api/accounts` results with a short TTL**
- **File**: `app/modules/accounts/service.py`
- **Change**: Add a small TTL cache around `list_accounts()` output (tune TTL to your freshness needs).
- **Impact**: Flattens spikes: concurrent requests share one DB fetch per TTL window.
- **Risk**: Low (stale reads). Add an explicit invalidation call on account mutations.

```python
from cachetools import TTLCache

class AccountsService:
    _accounts_cache: TTLCache = TTLCache(maxsize=1, ttl=30)
    
    async def list_accounts(self) -> list[AccountSummary]:
        cache_key = "accounts_list"
        if cache_key in self._accounts_cache:
            return self._accounts_cache[cache_key]
        
        accounts = await self._repo.list_accounts()
        # ... build summaries
        
        self._accounts_cache[cache_key] = result
        return result
    
    def invalidate_cache(self):
        self._accounts_cache.clear()
```

**Task 4.2: Cache usage “latest by account” for a shorter TTL**
- **Change**: Separate TTL for primary/secondary usage (e.g. 1–10s) to reduce repeated calls.
- **Rationale**: Usage changes more often than account metadata, but most UI interactions can tolerate a few seconds.

### Phase 5: Reduce SQLite Write Lock Pressure (Medium Effort, High ROI Under Concurrency)

If you observe lock waits or high tail latency during bursts, reduce the number of transactions and commits.

**Task 5.1: Batch usage writes (commit once per refresh loop)**
- **Files**: `app/modules/usage/repository.py`, `app/modules/usage/updater.py`
- **Change**: Avoid `commit()` per `UsageHistory` row; insert multiple rows then commit once per refresh loop (or per account).
- **Impact**: Fewer write transactions → fewer lock acquisitions → better concurrency.
- **Risk**: Medium (transaction semantics), but testable.

**Task 5.2: Audit request logging writes**
- **Change**: Confirm `request_logs` writes are not doing unnecessary per-request commits beyond what’s required.
- **Impact**: Under heavy proxy traffic, request logging can dominate SQLite writer time.
- **Risk**: Medium (behavioral expectations).

### Phase 6: Connection/SQLite Tuning (Small Wins, Situational)

**Task 6.1: Pool sizing**
- For SQLite, pool size rarely fixes single-writer contention; it mainly affects how many concurrent connections are opened.
- Still, confirm `database_pool_size` is not causing a thundering herd of connections under load.

**Task 6.2: Busy timeout**
- Treat `_SQLITE_BUSY_TIMEOUT_MS` as a tail-latency vs error-rate knob.
- Lowering it can reduce “requests stuck for 5s”, but may increase `SQLITE_BUSY` errors on writes.

## Expected Results

| Phase | Change | Expected Improvement |
|-------|--------|---------------------|
| 1 | Return 1 usage row/account | Large latency drop; less DB+Python work |
| 2 | Window-aware index | Faster “latest” lookups as data grows |
| 4 | Caching | Very large under read concurrency |
| 5 | Batch writes | Better tail latency under load |

**Cumulative Impact**: After Phases 1–4, `/api/accounts` should stop scaling with `usage_history` size and behave close to O(accounts). If concurrency is still limited by the DB writer, Phase 5 (batch writes) and/or Postgres become the next lever.

## Related (Proxy Hot Path)

If you’re seeing slowness on `GET /accounts` (SPA) or proxy endpoints under load, the two most common “low hanging fruit” issues are:
- **Don’t refresh usage on request**: `GET /api/codex/usage` should not call `UsageUpdater.refresh_accounts()` inline; rely on the background scheduler.
- **Keep routing state in-process**: selection should be cheap (snapshot TTL), and the snapshot should invalidate on rate-limit/quota/permanent-failure events.

Additional pragmatic defaults for a **single-instance** deployment:
- Prefer in-process stickiness: `CODEX_LB_STICKY_SESSIONS_BACKEND=memory` (avoids SQLite writes on the hot path). If you run multiple processes/workers, use `db`.
- Prefer buffered request logs: `CODEX_LB_REQUEST_LOGS_BUFFER_ENABLED=true` (reduces per-request commits; logs become eventually persisted).

## Rollback Strategy

Each phase is independent and can be rolled back:
1. **Query changes**: Revert repository implementation
2. **Indexes**: `DROP INDEX IF EXISTS`
3. **Caching**: Disable by removing cache wrapper / TTL=0
4. **Batch writes**: Revert to per-row commit semantics

## Testing Plan

1. **Load Testing**: Use `wrk` or `ab` with 10 concurrent connections
   ```bash
   wrk -t10 -c10 -d30s http://localhost:8080/api/accounts
   ```

2. **Correctness**: Validate the “latest per account” results match the previous logic for both windows.
3. **Query Performance**: Capture query plan + timing (EXPLAIN + timings) before/after.

4. **Cache Hit Rate**: Monitor cache effectiveness under load (ensure invalidation on mutations).

## Success Criteria

- [ ] P95 latency < 200ms with 10 concurrent requests
- [ ] P99 latency < 500ms with 10 concurrent requests
- [ ] Acceptable `SQLITE_BUSY` error rate (ideally zero for reads; writes depend on busy_timeout policy)

