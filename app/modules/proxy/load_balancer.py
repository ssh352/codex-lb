from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from app.core import usage as usage_core
from app.core.balancer import (
    AccountState,
    SelectionResult,
    handle_permanent_failure,
    handle_quota_exceeded,
    handle_rate_limit,
    handle_usage_limit_reached,
    select_account,
)
from app.core.balancer.debug import ineligibility_reason
from app.core.balancer.types import UpstreamError
from app.core.config.settings import get_settings
from app.core.metrics import get_metrics
from app.core.usage.quota import apply_usage_quota
from app.core.utils.request_id import get_request_id
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts.repository import AccountsRepository, AccountStatusUpdate
from app.modules.proxy.repo_bundle import ProxyRepoFactory
from app.modules.proxy.sticky_repository import StickySessionsRepository

logger = logging.getLogger(__name__)
UTC = timezone.utc


@dataclass
class RuntimeState:
    reset_at: float | None = None
    cooldown_until: float | None = None
    last_error_at: float | None = None
    last_selected_at: float | None = None
    error_count: int = 0


@dataclass
class AccountSelection:
    account: Account | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class LoadBalancerSelectionEvent:
    ts_epoch: float
    request_id: str | None
    pool: str
    sticky_backend: str
    reallocate_sticky: bool
    outcome: str
    reason_code: str | None
    selected_account_id: str | None
    error_message: str | None
    fallback_from_pinned: bool
    selected_tier: str | None
    tier_scores: tuple[LoadBalancerTierScore, ...]
    selected_secondary_reset_at: int | None
    selected_secondary_used_percent: float | None
    selected_primary_used_percent: float | None


@dataclass(frozen=True, slots=True)
class LoadBalancerTierScore:
    tier: str
    urgency: float
    weight: float
    score: float
    min_reset_at: int | None
    remaining_credits: float
    account_count: int


@dataclass(frozen=True, slots=True)
class LoadBalancerDebugAccountRow:
    account_id: str
    email: str
    plan_type: str
    status: str
    deactivation_reason: str | None
    db_reset_at: int | None


@dataclass(frozen=True, slots=True)
class LoadBalancerDebugDump:
    snapshot_updated_at_seconds: float
    sticky_backend: str
    pinned_account_ids: tuple[str, ...]
    account_rows: list[LoadBalancerDebugAccountRow]
    accounts_by_id: dict[str, LoadBalancerDebugAccountRow]
    latest_primary: dict[str, _UsageSnapshot]
    latest_secondary: dict[str, _UsageSnapshot]
    selection_reset_at: dict[str, float | None]
    cooldown_until: dict[str, float | None]
    last_error_at: dict[str, float | None]
    last_selected_at: dict[str, float | None]
    error_count: dict[str, int]
    pinned_ineligibility: dict[str, str | None]
    full_ineligibility: dict[str, str | None]
    sticky_counts: dict[str, int]


@dataclass(slots=True)
class _Snapshot:
    accounts: list[Account]
    latest_primary: dict[str, _UsageSnapshot]
    latest_secondary: dict[str, _UsageSnapshot]
    states: list[AccountState]
    account_map: dict[str, Account]
    pinned_account_ids: frozenset[str]
    updated_at: float


class LoadBalancer:
    def __init__(self, repo_factory: ProxyRepoFactory) -> None:
        self._repo_factory = repo_factory
        self._runtime: dict[str, RuntimeState] = {}
        self._snapshot_lock = asyncio.Lock()
        self._snapshot: _Snapshot | None = None
        self._snapshot_ttl_seconds = get_settings().proxy_snapshot_ttl_seconds
        self._pinned_settings_checked_at: float = 0.0
        self._pinned_settings_cached_ids: tuple[str, ...] | None = None
        self._sticky_lock = asyncio.Lock()
        self._sticky_memory: OrderedDict[str, _StickyEntry] = OrderedDict()
        self._debug_events: deque[LoadBalancerSelectionEvent] = deque(maxlen=get_settings().debug_lb_event_buffer_size)
        self._last_pinned_fallback_log_at: float = 0.0

    def invalidate_snapshot(self) -> None:
        self._snapshot = None

    async def select_forced_account(self, account_id: str) -> AccountSelection:
        # Force selection bypasses eligibility checks and pinned-pool logic. This is intended for
        # operator debugging ("what does this specific upstream account return right now?") where
        # normal failover/pinning would otherwise hide the target account's behavior.
        await self._maybe_invalidate_snapshot_on_pinned_change()
        snapshot = await self._get_snapshot()
        selected = snapshot.account_map.get(account_id)
        if selected is None:
            return AccountSelection(account=None, error_message="Forced account not found")

        selected_snapshot = _clone_account(selected)
        runtime = self._runtime.setdefault(selected_snapshot.id, RuntimeState())
        runtime.last_selected_at = time.time()
        return AccountSelection(account=selected_snapshot, error_message=None)

    def debug_events(self, *, limit: int) -> list[LoadBalancerSelectionEvent]:
        capped = max(0, min(int(limit), len(self._debug_events)))
        if capped <= 0:
            return []
        items = list(self._debug_events)
        return list(reversed(items[-capped:]))

    def _append_debug_event(
        self,
        *,
        pool: str,
        sticky_backend: str,
        reallocate_sticky: bool,
        result: SelectionResult,
        fallback_from_pinned: bool,
    ) -> None:
        trace = result.trace
        score_tuples: tuple[tuple[str, float], ...] = (
            tuple((score.tier, score.score) for score in trace.tier_scores) if trace is not None else tuple()
        )
        get_metrics().observe_lb_tier_decision(
            pool=pool,
            sticky_backend=sticky_backend,
            reallocate_sticky=reallocate_sticky,
            outcome=_selection_outcome(result),
            selected_tier=trace.selected_tier if trace is not None else None,
            tier_scores=score_tuples,
        )
        tier_scores = (
            tuple(
                LoadBalancerTierScore(
                    tier=score.tier,
                    urgency=score.urgency,
                    weight=score.weight,
                    score=score.score,
                    min_reset_at=score.min_reset_at,
                    remaining_credits=score.remaining_credits,
                    account_count=score.account_count,
                )
                for score in trace.tier_scores
            )
            if trace is not None
            else tuple()
        )
        self._debug_events.append(
            LoadBalancerSelectionEvent(
                ts_epoch=time.time(),
                request_id=get_request_id(),
                pool=pool,
                sticky_backend=sticky_backend,
                reallocate_sticky=reallocate_sticky,
                outcome=_selection_outcome(result),
                reason_code=result.reason_code,
                selected_account_id=result.account.account_id if result.account is not None else None,
                error_message=result.error_message,
                fallback_from_pinned=fallback_from_pinned,
                selected_tier=trace.selected_tier if trace is not None else None,
                tier_scores=tier_scores,
                selected_secondary_reset_at=trace.selected_secondary_reset_at if trace is not None else None,
                selected_secondary_used_percent=trace.selected_secondary_used_percent if trace is not None else None,
                selected_primary_used_percent=trace.selected_primary_used_percent if trace is not None else None,
            )
        )

    async def debug_dump(self) -> LoadBalancerDebugDump:
        await self._maybe_invalidate_snapshot_on_pinned_change()
        snapshot = await self._get_snapshot()
        settings = get_settings()

        sticky_backend_setting = settings.sticky_sessions_backend
        sticky_backend = sticky_backend_setting if sticky_backend_setting in {"db", "memory"} else "none"

        account_rows: list[LoadBalancerDebugAccountRow] = []
        accounts_by_id: dict[str, LoadBalancerDebugAccountRow] = {}
        for account in snapshot.accounts:
            row = LoadBalancerDebugAccountRow(
                account_id=account.id,
                email=account.email,
                plan_type=account.plan_type,
                status=str(account.status.value if hasattr(account.status, "value") else account.status),
                deactivation_reason=account.deactivation_reason,
                db_reset_at=account.reset_at,
            )
            account_rows.append(row)
            accounts_by_id[account.id] = row

        now = time.time()
        pinned_active = bool(snapshot.pinned_account_ids)
        pinned_cached = self._pinned_settings_cached_ids or tuple()
        if pinned_cached:
            pinned_ids = tuple(account_id for account_id in pinned_cached if account_id in snapshot.account_map)
        else:
            pinned_ids = tuple(sorted(snapshot.pinned_account_ids))

        full_ineligibility: dict[str, str | None] = {}
        pinned_ineligibility: dict[str, str | None] = {}
        selection_reset_at: dict[str, float | None] = {}
        cooldown_until: dict[str, float | None] = {}
        last_error_at: dict[str, float | None] = {}
        last_selected_at: dict[str, float | None] = {}
        error_count: dict[str, int] = {}

        for state in snapshot.states:
            reason = ineligibility_reason(state, now=now)
            full_ineligibility[state.account_id] = reason
            selection_reset_at[state.account_id] = state.reset_at
            cooldown_until[state.account_id] = state.cooldown_until
            last_error_at[state.account_id] = state.last_error_at
            last_selected_at[state.account_id] = state.last_selected_at
            error_count[state.account_id] = int(state.error_count)

        if pinned_active:
            pinned_set = set(snapshot.pinned_account_ids)
            for state in snapshot.states:
                if state.account_id not in pinned_set:
                    pinned_ineligibility[state.account_id] = "not_pinned"
                else:
                    pinned_ineligibility[state.account_id] = ineligibility_reason(state, now=now)
        else:
            pinned_ineligibility = dict(full_ineligibility)

        sticky_counts: dict[str, int] = {}
        if sticky_backend == "memory":
            now = time.time()
            async with self._sticky_lock:
                for entry in self._sticky_memory.values():
                    if entry.expires_at <= now:
                        continue
                    sticky_counts[entry.account_id] = sticky_counts.get(entry.account_id, 0) + 1

        return LoadBalancerDebugDump(
            snapshot_updated_at_seconds=snapshot.updated_at,
            sticky_backend=sticky_backend,
            pinned_account_ids=pinned_ids,
            account_rows=account_rows,
            accounts_by_id=accounts_by_id,
            latest_primary=snapshot.latest_primary,
            latest_secondary=snapshot.latest_secondary,
            selection_reset_at=selection_reset_at,
            cooldown_until=cooldown_until,
            last_error_at=last_error_at,
            last_selected_at=last_selected_at,
            error_count=error_count,
            pinned_ineligibility=pinned_ineligibility,
            full_ineligibility=full_ineligibility,
            sticky_counts=sticky_counts,
        )

    async def select_account(
        self,
        sticky_key: str | None = None,
        *,
        reallocate_sticky: bool = False,
    ) -> AccountSelection:
        await self._maybe_invalidate_snapshot_on_pinned_change()
        snapshot = await self._get_snapshot()
        original_account_fields: dict[str, tuple[AccountStatus, str | None, int | None]] = {
            account_id: (account.status, account.deactivation_reason, account.reset_at)
            for account_id, account in snapshot.account_map.items()
        }
        selected_snapshot: Account | None = None
        error_message: str | None = None
        # Routing pool ("pinned accounts") is applied before stickiness:
        # - When `pinned_account_ids` is non-empty, only pinned accounts are eligible candidates.
        # - A sticky mapping is only honored if it points to an eligible (pinned) account; otherwise the
        #   sticky entry is dropped and the key is reassigned on selection.
        # - If the pinned pool yields no eligible accounts (e.g. all paused/deactivated/limited), routing
        #   falls back to selecting from the full account set.
        #
        # Tier-aware scoring influences *which* account is chosen when a sticky key is first assigned
        # (or explicitly reallocated), but stickiness does not proactively migrate just because some
        # other account later becomes a "better" selector candidate.
        pinned_active = bool(snapshot.pinned_account_ids)
        pinned_states = (
            [state for state in snapshot.states if state.account_id in snapshot.pinned_account_ids]
            if pinned_active
            else snapshot.states
        )

        settings = get_settings()
        sticky_backend_setting = settings.sticky_sessions_backend
        if not sticky_key:
            sticky_backend = "none"
        elif sticky_backend_setting in {"db", "memory"}:
            sticky_backend = sticky_backend_setting
        else:
            sticky_backend = "none"
        if sticky_key and sticky_backend == "db":
            async with self._repo_factory() as repos:
                result = await self._select_with_stickiness(
                    states=pinned_states,
                    account_map=snapshot.account_map,
                    sticky_key=sticky_key,
                    reallocate_sticky=reallocate_sticky,
                    sticky_repo=repos.sticky_sessions,
                )
                get_metrics().observe_lb_select(
                    pool="pinned" if pinned_active else "full",
                    sticky_backend=sticky_backend,
                    reallocate_sticky=reallocate_sticky,
                    outcome=_selection_outcome(result),
                )
                self._append_debug_event(
                    pool="pinned" if pinned_active else "full",
                    sticky_backend=sticky_backend,
                    reallocate_sticky=reallocate_sticky,
                    result=result,
                    fallback_from_pinned=False,
                )
                if pinned_active and result.account is None:
                    pinned_result = result
                    result = await self._select_with_stickiness(
                        states=snapshot.states,
                        account_map=snapshot.account_map,
                        sticky_key=sticky_key,
                        reallocate_sticky=reallocate_sticky,
                        sticky_repo=repos.sticky_sessions,
                    )
                    get_metrics().observe_lb_select(
                        pool="full",
                        sticky_backend=sticky_backend,
                        reallocate_sticky=reallocate_sticky,
                        outcome=_selection_outcome(result),
                    )
                    self._append_debug_event(
                        pool="full",
                        sticky_backend=sticky_backend,
                        reallocate_sticky=reallocate_sticky,
                        result=result,
                        fallback_from_pinned=True,
                    )
                    self._maybe_log_pinned_fallback(
                        pinned_states=pinned_states,
                        account_map=snapshot.account_map,
                        pinned_result=pinned_result,
                        full_result=result,
                        sticky_backend=sticky_backend,
                        reallocate_sticky=reallocate_sticky,
                    )
        elif sticky_key and sticky_backend == "memory":
            result = await self._select_with_memory_stickiness(
                states=pinned_states,
                account_map=snapshot.account_map,
                sticky_key=sticky_key,
                reallocate_sticky=reallocate_sticky,
            )
            get_metrics().observe_lb_select(
                pool="pinned" if pinned_active else "full",
                sticky_backend=sticky_backend,
                reallocate_sticky=reallocate_sticky,
                outcome=_selection_outcome(result),
            )
            self._append_debug_event(
                pool="pinned" if pinned_active else "full",
                sticky_backend=sticky_backend,
                reallocate_sticky=reallocate_sticky,
                result=result,
                fallback_from_pinned=False,
            )
            if pinned_active and result.account is None:
                pinned_result = result
                result = await self._select_with_memory_stickiness(
                    states=snapshot.states,
                    account_map=snapshot.account_map,
                    sticky_key=sticky_key,
                    reallocate_sticky=reallocate_sticky,
                )
                get_metrics().observe_lb_select(
                    pool="full",
                    sticky_backend=sticky_backend,
                    reallocate_sticky=reallocate_sticky,
                    outcome=_selection_outcome(result),
                )
                self._append_debug_event(
                    pool="full",
                    sticky_backend=sticky_backend,
                    reallocate_sticky=reallocate_sticky,
                    result=result,
                    fallback_from_pinned=True,
                )
                self._maybe_log_pinned_fallback(
                    pinned_states=pinned_states,
                    account_map=snapshot.account_map,
                    pinned_result=pinned_result,
                    full_result=result,
                    sticky_backend=sticky_backend,
                    reallocate_sticky=reallocate_sticky,
                )
        else:
            result = select_account(pinned_states)
            get_metrics().observe_lb_select(
                pool="pinned" if pinned_active else "full",
                sticky_backend=sticky_backend,
                reallocate_sticky=reallocate_sticky,
                outcome=_selection_outcome(result),
            )
            self._append_debug_event(
                pool="pinned" if pinned_active else "full",
                sticky_backend=sticky_backend,
                reallocate_sticky=reallocate_sticky,
                result=result,
                fallback_from_pinned=False,
            )
            if pinned_active and result.account is None:
                pinned_result = result
                result = select_account(snapshot.states)
                get_metrics().observe_lb_select(
                    pool="full",
                    sticky_backend=sticky_backend,
                    reallocate_sticky=reallocate_sticky,
                    outcome=_selection_outcome(result),
                )
                self._append_debug_event(
                    pool="full",
                    sticky_backend=sticky_backend,
                    reallocate_sticky=reallocate_sticky,
                    result=result,
                    fallback_from_pinned=True,
                )

                self._maybe_log_pinned_fallback(
                    pinned_states=pinned_states,
                    account_map=snapshot.account_map,
                    pinned_result=pinned_result,
                    full_result=result,
                    sticky_backend=sticky_backend,
                    reallocate_sticky=reallocate_sticky,
                )

        if result.account is None:
            error_message = result.error_message
        else:
            sync_needed = False
            for state in snapshot.states:
                before = original_account_fields.get(state.account_id)
                if before is None:
                    continue
                before_status, before_reason, before_reset_at = before
                state_reset_at = int(state.reset_at) if state.reset_at is not None else None
                if (
                    before_status != state.status
                    or before_reason != state.deactivation_reason
                    or before_reset_at != state_reset_at
                ):
                    sync_needed = True
                    break

            if sync_needed:
                try:
                    async with self._repo_factory() as repos:
                        await self._sync_usage_statuses(repos.accounts, snapshot.account_map, snapshot.states)
                except Exception:
                    logger.exception("lb_status_reconcile_failed request_id=%s", get_request_id())
                    for state in snapshot.states:
                        account = snapshot.account_map.get(state.account_id)
                        if account is None:
                            continue
                        account.status = state.status
                        account.deactivation_reason = state.deactivation_reason
                        account.reset_at = int(state.reset_at) if state.reset_at is not None else None

            selected = snapshot.account_map.get(result.account.account_id)
            if selected is None:
                error_message = result.error_message
            else:
                selected_snapshot = _clone_account(selected)

        if selected_snapshot is None:
            return AccountSelection(account=None, error_message=error_message)

        runtime = self._runtime.setdefault(selected_snapshot.id, RuntimeState())
        runtime.last_selected_at = time.time()
        return AccountSelection(account=selected_snapshot, error_message=None)

    def _maybe_log_pinned_fallback(
        self,
        *,
        pinned_states: list[AccountState],
        account_map: dict[str, Account],
        pinned_result: SelectionResult | None,
        full_result: SelectionResult,
        sticky_backend: str,
        reallocate_sticky: bool,
    ) -> None:
        now = time.time()
        # Avoid writing a log line per request when pinned pool remains unavailable.
        if now - self._last_pinned_fallback_log_at < 10.0:
            return
        self._last_pinned_fallback_log_at = now

        reason_counts: dict[str, int] = {}
        for state in pinned_states:
            reason = ineligibility_reason(state, now=now) or "eligible"
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        pinned_outcome = _selection_outcome(pinned_result) if pinned_result is not None else "unknown"
        pinned_reason_code = pinned_result.reason_code if pinned_result is not None else None
        pinned_error_message = pinned_result.error_message if pinned_result is not None else None

        selected_display: str | None = None
        if full_result.account is not None:
            selected = account_map.get(full_result.account.account_id)
            if selected is not None:
                selected_display = f"{selected.email}[{selected.id[:3]}]"

        logger.info(
            "lb_fallback pinned_failed pinned_size=%s pinned_outcome=%s pinned_reason_code=%s "
            "pinned_error=%s reasons=%s full_outcome=%s full_selected=%s sticky_backend=%s "
            "reallocate=%s request_id=%s",
            len(pinned_states),
            pinned_outcome,
            pinned_reason_code,
            pinned_error_message,
            ",".join(f"{k}={v}" for k, v in sorted(reason_counts.items())),
            _selection_outcome(full_result),
            selected_display,
            sticky_backend,
            reallocate_sticky,
            get_request_id(),
        )

    async def _maybe_invalidate_snapshot_on_pinned_change(self) -> None:
        snapshot = self._snapshot
        if snapshot is None:
            return
        now = time.time()
        if (now - snapshot.updated_at) >= self._snapshot_ttl_seconds:
            return
        if (now - self._pinned_settings_checked_at) < 1.0:
            return
        self._pinned_settings_checked_at = now

        try:
            async with self._repo_factory() as repos:
                pinned = await repos.settings.pinned_account_ids()
        except Exception:
            return

        pinned_tuple = tuple(pinned)
        if self._pinned_settings_cached_ids is None:
            self._pinned_settings_cached_ids = pinned_tuple
            return
        if pinned_tuple == self._pinned_settings_cached_ids:
            return
        self._pinned_settings_cached_ids = pinned_tuple
        self._snapshot = None

    async def _get_snapshot(self) -> _Snapshot:
        now = time.time()
        snapshot = self._snapshot
        if snapshot is not None and (now - snapshot.updated_at) < self._snapshot_ttl_seconds:
            return snapshot

        async with self._snapshot_lock:
            now = time.time()
            snapshot = self._snapshot
            if snapshot is not None and (now - snapshot.updated_at) < self._snapshot_ttl_seconds:
                return snapshot

            async with self._repo_factory() as repos:
                accounts_orm = await repos.accounts.list_accounts()
                latest_primary_orm, latest_secondary_orm = await repos.usage.latest_primary_secondary_by_account()

                latest_primary = _usage_snapshots(latest_primary_orm)
                latest_secondary = _usage_snapshots(latest_secondary_orm)

                states, account_map = _build_states(
                    accounts=accounts_orm,
                    latest_primary=latest_primary,
                    latest_secondary=latest_secondary,
                    runtime=self._runtime,
                )
                await self._sync_usage_statuses(repos.accounts, account_map, states)
                pinned_raw = await repos.settings.pinned_account_ids()
                quota_exceeded_ids = {
                    state.account_id for state in states if state.status == AccountStatus.QUOTA_EXCEEDED
                }
                pinned_prune = [account_id for account_id in pinned_raw if account_id in quota_exceeded_ids]
                if pinned_prune:
                    await repos.settings.remove_pinned_account_ids(pinned_prune)
                    pinned_raw = [account_id for account_id in pinned_raw if account_id not in set(pinned_prune)]
                self._pinned_settings_cached_ids = tuple(pinned_raw)
                self._pinned_settings_checked_at = now
                pinned_account_ids = frozenset(account_id for account_id in pinned_raw if account_id in account_map)
                accounts = [_clone_account(account) for account in accounts_orm]
                account_map = {account.id: account for account in accounts}

            snapshot = _Snapshot(
                accounts=accounts,
                latest_primary=latest_primary,
                latest_secondary=latest_secondary,
                states=states,
                account_map=account_map,
                pinned_account_ids=pinned_account_ids,
                updated_at=now,
            )
            get_metrics().observe_lb_snapshot_refresh(updated_at_seconds=now)
            self._snapshot = snapshot
            return snapshot

    async def _select_with_stickiness(
        self,
        *,
        states: list[AccountState],
        account_map: dict[str, Account],
        sticky_key: str | None,
        reallocate_sticky: bool,
        sticky_repo: StickySessionsRepository | None,
    ) -> SelectionResult:
        if not sticky_key or not sticky_repo:
            return select_account(states)

        if reallocate_sticky:
            chosen = select_account(states)
            if chosen.account is not None and chosen.account.account_id in account_map:
                await sticky_repo.upsert(sticky_key, chosen.account.account_id)
            return chosen

        existing = await sticky_repo.get_account_id(sticky_key)
        if existing:
            pinned = next((state for state in states if state.account_id == existing), None)
            if pinned is None:
                await sticky_repo.delete(sticky_key)
            else:
                # Stickiness is honored as long as the pinned account remains eligible.
                # Note: we do not proactively reassign on secondary reset boundaries; reassignment
                # typically happens on retry (explicit reallocation) or when the pinned account
                # becomes unavailable/ineligible. In particular, we do not reassign just because the
                # selector score would prefer a different account.
                pinned_result = select_account([pinned])
                if pinned_result.account is not None:
                    return pinned_result

        chosen = select_account(states)
        if chosen.account is not None and chosen.account.account_id in account_map:
            await sticky_repo.upsert(sticky_key, chosen.account.account_id)
        return chosen

    async def _select_with_memory_stickiness(
        self,
        *,
        states: list[AccountState],
        account_map: dict[str, Account],
        sticky_key: str,
        reallocate_sticky: bool,
    ) -> SelectionResult:
        if reallocate_sticky:
            chosen = select_account(states)
            if chosen.account is not None and chosen.account.account_id in account_map:
                await self._sticky_set(sticky_key, chosen.account.account_id)
            return chosen

        existing = await self._sticky_get(sticky_key)
        if existing:
            pinned = next((state for state in states if state.account_id == existing), None)
            if pinned is None:
                await self._sticky_delete(sticky_key)
            else:
                # Stickiness is honored as long as the pinned account remains eligible.
                # Note: we do not proactively reassign on secondary reset boundaries; reassignment
                # typically happens on retry (explicit reallocation) or when the pinned account
                # becomes unavailable/ineligible. In particular, we do not reassign just because the
                # selector score would prefer a different account.
                pinned_result = select_account([pinned])
                if pinned_result.account is not None:
                    return pinned_result

        chosen = select_account(states)
        if chosen.account is not None and chosen.account.account_id in account_map:
            await self._sticky_set(sticky_key, chosen.account.account_id)
        return chosen

    async def mark_rate_limit(self, account: Account, error: UpstreamError) -> None:
        state = self._state_for(account)
        handle_rate_limit(state, error)
        logger.info(
            "lb_mark event=rate_limit account=%s[%s] error_count=%s cooldown_until=%s reset_at=%s request_id=%s",
            account.email,
            account.id[:3],
            state.error_count,
            _dt_iso(_dt_from_epoch(state.cooldown_until)),
            _dt_iso(_dt_from_epoch(state.reset_at)),
            get_request_id(),
        )
        async with self._repo_factory() as repos:
            await self._sync_state(repos.accounts, account, state)
        get_metrics().observe_lb_mark(event="rate_limit", account_id=account.id)
        self._snapshot = None

    async def mark_usage_limit_reached(self, account: Account, error: UpstreamError) -> None:
        state = self._state_for(account)
        async with self._repo_factory() as repos:
            try:
                latest_secondary = await repos.usage.latest_by_account(window="secondary")
            except Exception:
                latest_secondary = {}
            entry = latest_secondary.get(account.id)
            if entry is not None:
                state.secondary_used_percent = float(entry.used_percent)
                state.secondary_reset_at = entry.reset_at
                state.secondary_capacity_credits = usage_core.capacity_for_plan(account.plan_type, "secondary")

            handle_usage_limit_reached(state, error)
            logger.info(
                "lb_mark event=usage_limit_reached account=%s[%s] error_count=%s cooldown_until=%s reset_at=%s "
                "request_id=%s",
                account.email,
                account.id[:3],
                state.error_count,
                _dt_iso(_dt_from_epoch(state.cooldown_until)),
                _dt_iso(_dt_from_epoch(state.reset_at)),
                get_request_id(),
            )
            await self._sync_state(repos.accounts, account, state)
        # Keep metrics compatibility: `usage_limit_reached` is treated as a rate-limit-like mark.
        get_metrics().observe_lb_mark(event="rate_limit", account_id=account.id)
        self._snapshot = None

    async def mark_quota_exceeded(self, account: Account, error: UpstreamError) -> None:
        state = self._state_for(account)
        handle_quota_exceeded(state, error)
        async with self._repo_factory() as repos:
            await self._sync_state(repos.accounts, account, state)
            await repos.settings.remove_pinned_account_ids([account.id])
        get_metrics().observe_lb_mark(event="quota_exceeded", account_id=account.id)
        self._snapshot = None

    async def mark_permanent_failure(self, account: Account, error_code: str) -> None:
        state = self._state_for(account)
        handle_permanent_failure(state, error_code)
        async with self._repo_factory() as repos:
            await self._sync_state(repos.accounts, account, state)
        get_metrics().observe_lb_mark(event="permanent_failure", account_id=account.id)
        get_metrics().observe_lb_permanent_failure(code=error_code)
        self._snapshot = None

    async def record_error(self, account: Account) -> None:
        state = self._state_for(account)
        state.error_count += 1
        state.last_error_at = time.time()
        async with self._repo_factory() as repos:
            await self._sync_state(repos.accounts, account, state)
        get_metrics().observe_lb_mark(event="error", account_id=account.id)
        self._snapshot = None

    def _state_for(self, account: Account) -> AccountState:
        runtime = self._runtime.setdefault(account.id, RuntimeState())
        reset_at = runtime.reset_at
        if reset_at is None and account.reset_at:
            reset_at = float(account.reset_at)
        return AccountState(
            account_id=account.id,
            status=account.status,
            plan_type=account.plan_type,
            used_percent=None,
            reset_at=reset_at,
            cooldown_until=runtime.cooldown_until,
            secondary_used_percent=None,
            secondary_reset_at=None,
            last_error_at=runtime.last_error_at,
            last_selected_at=runtime.last_selected_at,
            error_count=runtime.error_count,
            deactivation_reason=account.deactivation_reason,
        )

    async def _sync_state(
        self,
        accounts_repo: AccountsRepository,
        account: Account,
        state: AccountState,
    ) -> None:
        runtime = self._runtime.setdefault(account.id, RuntimeState())
        runtime.reset_at = state.reset_at
        runtime.cooldown_until = state.cooldown_until
        runtime.last_error_at = state.last_error_at
        runtime.error_count = state.error_count

        reset_at_int = int(state.reset_at) if state.reset_at else None
        status_changed = account.status != state.status
        reason_changed = account.deactivation_reason != state.deactivation_reason
        reset_changed = account.reset_at != reset_at_int

        if status_changed or reason_changed or reset_changed:
            await accounts_repo.update_status(
                account.id,
                state.status,
                state.deactivation_reason,
                reset_at_int,
            )
            account.status = state.status
            account.deactivation_reason = state.deactivation_reason
            account.reset_at = reset_at_int

    async def _sync_usage_statuses(
        self,
        accounts_repo: AccountsRepository,
        account_map: dict[str, Account],
        states: Iterable[AccountState],
    ) -> None:
        updates: list[AccountStatusUpdate] = []
        for state in states:
            account = account_map.get(state.account_id)
            if not account:
                continue
            reset_at_int = int(state.reset_at) if state.reset_at is not None else None
            status_changed = account.status != state.status
            reason_changed = account.deactivation_reason != state.deactivation_reason
            reset_changed = account.reset_at != reset_at_int
            if status_changed or reason_changed or reset_changed:
                updates.append(
                    AccountStatusUpdate(
                        account_id=account.id,
                        status=state.status,
                        deactivation_reason=state.deactivation_reason,
                        reset_at=reset_at_int,
                    )
                )
                account.status = state.status
                account.deactivation_reason = state.deactivation_reason
                account.reset_at = reset_at_int
        if updates:
            await accounts_repo.bulk_update_status_fields(updates)

    async def _sticky_get(self, key: str) -> str | None:
        now = time.time()
        async with self._sticky_lock:
            entry = self._sticky_memory.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._sticky_memory.pop(key, None)
                return None
            self._sticky_memory.move_to_end(key)
            return entry.account_id

    async def _sticky_set(self, key: str, account_id: str) -> None:
        settings = get_settings()
        expires_at = time.time() + settings.sticky_sessions_memory_ttl_seconds
        async with self._sticky_lock:
            self._sticky_memory[key] = _StickyEntry(account_id=account_id, expires_at=expires_at)
            self._sticky_memory.move_to_end(key)
            while len(self._sticky_memory) > settings.sticky_sessions_memory_maxsize:
                self._sticky_memory.popitem(last=False)

    async def _sticky_delete(self, key: str) -> None:
        async with self._sticky_lock:
            self._sticky_memory.pop(key, None)


def _build_states(
    *,
    accounts: Iterable[Account],
    latest_primary: dict[str, _UsageSnapshot],
    latest_secondary: dict[str, _UsageSnapshot],
    runtime: dict[str, RuntimeState],
) -> tuple[list[AccountState], dict[str, Account]]:
    states: list[AccountState] = []
    account_map: dict[str, Account] = {}

    for account in accounts:
        state = _state_from_account(
            account=account,
            primary_entry=latest_primary.get(account.id),
            secondary_entry=latest_secondary.get(account.id),
            runtime=runtime.setdefault(account.id, RuntimeState()),
        )
        states.append(state)
        account_map[account.id] = account
    return states, account_map


def _state_from_account(
    *,
    account: Account,
    primary_entry: _UsageSnapshot | None,
    secondary_entry: _UsageSnapshot | None,
    runtime: RuntimeState,
) -> AccountState:
    primary_used = primary_entry.used_percent if primary_entry else None
    primary_reset = primary_entry.reset_at if primary_entry else None
    primary_window_minutes = primary_entry.window_minutes if primary_entry else None
    secondary_used = secondary_entry.used_percent if secondary_entry else None
    secondary_reset = secondary_entry.reset_at if secondary_entry else None

    db_reset_at = float(account.reset_at) if account.reset_at else None
    effective_runtime_reset = runtime.reset_at or db_reset_at

    status, used_percent, reset_at = apply_usage_quota(
        status=account.status,
        primary_used=primary_used,
        primary_reset=primary_reset,
        primary_window_minutes=primary_window_minutes,
        runtime_reset=effective_runtime_reset,
        secondary_used=secondary_used,
        secondary_reset=secondary_reset,
    )

    return AccountState(
        account_id=account.id,
        status=status,
        plan_type=account.plan_type,
        used_percent=used_percent,
        reset_at=reset_at,
        cooldown_until=runtime.cooldown_until,
        secondary_used_percent=secondary_used,
        secondary_reset_at=secondary_reset,
        secondary_capacity_credits=usage_core.capacity_for_plan(account.plan_type, "secondary"),
        last_error_at=runtime.last_error_at,
        last_selected_at=runtime.last_selected_at,
        error_count=runtime.error_count,
        deactivation_reason=account.deactivation_reason,
    )


def _clone_account(account: Account) -> Account:
    data = {column.name: getattr(account, column.name) for column in Account.__table__.columns}
    return Account(**data)


@dataclass(frozen=True, slots=True)
class _UsageSnapshot:
    recorded_at: datetime | None
    used_percent: float
    reset_at: int | None
    window_minutes: int | None


@dataclass(frozen=True, slots=True)
class _StickyEntry:
    account_id: str
    expires_at: float


def _usage_snapshots(entries: dict[str, UsageHistory]) -> dict[str, _UsageSnapshot]:
    return {
        account_id: _UsageSnapshot(
            recorded_at=entry.recorded_at,
            used_percent=entry.used_percent,
            reset_at=entry.reset_at,
            window_minutes=entry.window_minutes,
        )
        for account_id, entry in entries.items()
    }


def _dt_from_epoch(value: float | int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=UTC)


def _dt_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _selection_outcome(result: SelectionResult) -> str:
    if result.account is not None:
        return "selected"
    match result.reason_code:
        case "paused_or_auth":
            return "paused_or_auth"
        case "paused":
            return "paused"
        case "auth":
            return "auth"
        case "quota_exceeded":
            return "quota_exceeded"
        case "rate_limited":
            return "rate_limited"
        case "cooldown":
            return "cooldown"
        case "no_available":
            return "no_available"
        case _:
            return "unknown"
