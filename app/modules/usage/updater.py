from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol

from app.core.auth import DEFAULT_PLAN
from app.core.auth.refresh import RefreshError
from app.core.clients.usage import UsageFetchError, fetch_usage
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.metrics import get_metrics
from app.core.plan_types import coerce_account_plan_type
from app.core.usage.models import UsagePayload
from app.core.utils.request_id import get_request_id
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts.auth_manager import AccountsRepositoryPort, AuthManager
from app.modules.accounts.list_cache import invalidate_accounts_list_cache

logger = logging.getLogger(__name__)


class UsageRepositoryPort(Protocol):
    async def add_entry(
        self,
        account_id: str,
        used_percent: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        recorded_at: datetime | None = None,
        window: str | None = None,
        reset_at: int | None = None,
        window_minutes: int | None = None,
        credits_has: bool | None = None,
        credits_unlimited: bool | None = None,
        credits_balance: float | None = None,
        *,
        commit: bool = True,
    ) -> UsageHistory | None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class UsageUpdater:
    def __init__(
        self,
        usage_repo: UsageRepositoryPort,
        accounts_repo: AccountsRepositoryPort | None = None,
    ) -> None:
        self._usage_repo = usage_repo
        self._accounts_repo = accounts_repo
        self._encryptor = TokenEncryptor()
        self._auth_manager = AuthManager(accounts_repo) if accounts_repo else None

    async def refresh_accounts(
        self,
        accounts: list[Account],
        latest_usage: Mapping[str, UsageHistory],
    ) -> None:
        settings = get_settings()
        if not settings.usage_refresh_enabled:
            return

        now = utcnow()
        interval = settings.usage_refresh_interval_seconds
        targets: list[_UsageRefreshTarget] = []
        account_map: dict[str, Account] = {}
        for account in accounts:
            account_map[account.id] = account
            if account.status == AccountStatus.DEACTIVATED:
                continue
            latest = latest_usage.get(account.id)
            if latest and (now - latest.recorded_at).total_seconds() < interval:
                continue
            targets.append(_UsageRefreshTarget.from_account(account))

        if not targets:
            return

        semaphore = asyncio.Semaphore(settings.usage_refresh_fetch_concurrency)
        fetch_results = await asyncio.gather(
            *(self._fetch_usage_target(target, semaphore) for target in targets),
            return_exceptions=False,
        )
        results_by_account = {result.account_id: result for result in fetch_results}

        for target in targets:
            account = account_map.get(target.account_id)
            if account is None:
                continue
            result = results_by_account.get(target.account_id)
            if result is None:
                continue

            # NOTE: AsyncSession is not safe for concurrent use. Apply DB writes
            # sequentially within the request-scoped session.
            try:
                if result.payload is not None:
                    await self._persist_payload(account, result.payload)
                    await self._usage_repo.commit()
                    continue

                if result.needs_auth_refresh:
                    await self._refresh_account(account, usage_account_id=account.chatgpt_account_id)
                    await self._usage_repo.commit()
                    continue
            except Exception as exc:
                await self._usage_repo.rollback()
                logger.warning(
                    "Usage refresh failed account_id=%s request_id=%s error=%s",
                    account.id,
                    get_request_id(),
                    exc,
                    exc_info=True,
                )
                continue

    async def _refresh_account(self, account: Account, *, usage_account_id: str | None) -> None:
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        payload: UsagePayload | None = None
        try:
            payload = await fetch_usage(
                access_token=access_token,
                account_id=usage_account_id,
            )
        except UsageFetchError as exc:
            # `initial_fetch` failures are already counted in `_fetch_usage_target` for this path.
            # Avoid double-counting when we enter the 401 refresh flow.
            if exc.status_code != 401 or not self._auth_manager:
                return
            try:
                account = await self._auth_manager.ensure_fresh(account, force=True)
            except RefreshError:
                return
            access_token = self._encryptor.decrypt(account.access_token_encrypted)
            try:
                payload = await fetch_usage(
                    access_token=access_token,
                    account_id=usage_account_id,
                )
            except UsageFetchError as retry_exc:
                get_metrics().observe_usage_refresh_failure(
                    status_code=retry_exc.status_code,
                    phase="post_refresh_retry",
                )
                # Keep this path single-retry only. Scheduler cadence (`usage_refresh_interval_seconds`)
                # is the global backoff control for background usage polling.
                logger.warning(
                    "Usage fetch retry failed account_id=%s status=%s message=%s request_id=%s",
                    account.id,
                    retry_exc.status_code,
                    retry_exc.message,
                    get_request_id(),
                )
                return

        if payload is None:
            return
        await self._persist_payload(account, payload)

    async def _persist_payload(self, account: Account, payload: UsagePayload) -> None:
        if self._accounts_repo is not None and payload.plan_type is not None:
            updated_plan_type = coerce_account_plan_type(payload.plan_type, account.plan_type or DEFAULT_PLAN)
            if updated_plan_type != account.plan_type:
                account.plan_type = updated_plan_type
                try:
                    await self._accounts_repo.update_tokens(
                        account.id,
                        access_token_encrypted=account.access_token_encrypted,
                        refresh_token_encrypted=account.refresh_token_encrypted,
                        id_token_encrypted=account.id_token_encrypted,
                        last_refresh=account.last_refresh,
                        plan_type=account.plan_type,
                        email=account.email,
                        chatgpt_account_id=account.chatgpt_account_id,
                    )
                    invalidate_accounts_list_cache()
                except Exception:
                    logger.warning(
                        "Failed to persist plan type from usage payload account_id=%s plan_type=%s request_id=%s",
                        account.id,
                        account.plan_type,
                        get_request_id(),
                        exc_info=True,
                    )

        rate_limit = payload.rate_limit
        if rate_limit is None:
            return

        primary = rate_limit.primary_window
        secondary = rate_limit.secondary_window
        credits_has, credits_unlimited, credits_balance = _credits_snapshot(payload)
        now_epoch = _now_epoch()

        recorded: set[str] = set()
        candidates = (("primary", primary), ("secondary", secondary))
        for default_window, window_payload in candidates:
            if window_payload is None or window_payload.used_percent is None:
                continue
            window_minutes = _window_minutes(window_payload.limit_window_seconds)
            effective_window = default_window
            if window_minutes is not None and window_minutes >= (24 * 60):
                effective_window = "secondary"
            if effective_window in recorded:
                continue
            recorded.add(effective_window)
            await self._usage_repo.add_entry(
                account_id=account.id,
                used_percent=float(window_payload.used_percent),
                input_tokens=None,
                output_tokens=None,
                window=effective_window,
                reset_at=_reset_at(
                    window_payload.reset_at,
                    window_payload.reset_after_seconds,
                    now_epoch,
                ),
                window_minutes=window_minutes,
                credits_has=credits_has if default_window == "primary" else None,
                credits_unlimited=credits_unlimited if default_window == "primary" else None,
                credits_balance=credits_balance if default_window == "primary" else None,
                commit=False,
            )

    async def _fetch_usage_target(
        self,
        target: _UsageRefreshTarget,
        semaphore: asyncio.Semaphore,
    ) -> _UsageFetchResult:
        async with semaphore:
            access_token = self._encryptor.decrypt(target.access_token_encrypted)
            try:
                payload = await fetch_usage(
                    access_token=access_token,
                    account_id=target.usage_account_id,
                )
                return _UsageFetchResult(
                    account_id=target.account_id,
                    payload=payload,
                    needs_auth_refresh=False,
                )
            except UsageFetchError as exc:
                # Usage endpoint failures are treated as observability signals, not account-liveness signals.
                # We intentionally avoid deactivating accounts here because `/wham/usage` can fail even when
                # proxy routing for the same account remains healthy.
                get_metrics().observe_usage_refresh_failure(status_code=exc.status_code, phase="initial_fetch")
                if exc.status_code == 401 and self._auth_manager is not None:
                    return _UsageFetchResult(
                        account_id=target.account_id,
                        payload=None,
                        needs_auth_refresh=True,
                    )
                return _UsageFetchResult(
                    account_id=target.account_id,
                    payload=None,
                    needs_auth_refresh=False,
                )


def _credits_snapshot(payload: UsagePayload) -> tuple[bool | None, bool | None, float | None]:
    credits = payload.credits
    if credits is None:
        return None, None, None
    credits_has = credits.has_credits
    credits_unlimited = credits.unlimited
    balance_value = credits.balance
    return credits_has, credits_unlimited, _parse_credits_balance(balance_value)


def _parse_credits_balance(value: str | int | float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _window_minutes(limit_seconds: int | None) -> int | None:
    if not limit_seconds or limit_seconds <= 0:
        return None
    return max(1, math.ceil(limit_seconds / 60))


def _now_epoch() -> int:
    return int(utcnow().replace(tzinfo=timezone.utc).timestamp())


def _reset_at(reset_at: int | None, reset_after_seconds: int | None, now_epoch: int) -> int | None:
    if reset_at is not None:
        return int(reset_at)
    if reset_after_seconds is None:
        return None
    return now_epoch + max(0, int(reset_after_seconds))


@dataclass(frozen=True, slots=True)
class _UsageRefreshTarget:
    account_id: str
    usage_account_id: str | None
    access_token_encrypted: bytes

    @classmethod
    def from_account(cls, account: Account) -> _UsageRefreshTarget:
        return cls(
            account_id=account.id,
            usage_account_id=account.chatgpt_account_id,
            access_token_encrypted=account.access_token_encrypted,
        )


@dataclass(frozen=True, slots=True)
class _UsageFetchResult:
    account_id: str
    payload: UsagePayload | None
    needs_auth_refresh: bool
