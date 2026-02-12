from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol

from app.core.auth.refresh import RefreshError
from app.core.clients.usage import UsageFetchError, fetch_usage
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
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
                if result.deactivate_error is not None:
                    await self._deactivate_for_client_error(account, result.deactivate_error)
                    continue

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
            if _should_deactivate_for_usage_error(exc.status_code):
                await self._deactivate_for_client_error(account, exc)
                return
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
                if _should_deactivate_for_usage_error(retry_exc.status_code):
                    await self._deactivate_for_client_error(account, retry_exc)
                return

        if payload is None:
            return
        await self._persist_payload(account, payload)

    async def _persist_payload(self, account: Account, payload: UsagePayload) -> None:
        rate_limit = payload.rate_limit
        if rate_limit is None:
            return

        primary = rate_limit.primary_window
        secondary = rate_limit.secondary_window
        credits_has, credits_unlimited, credits_balance = _credits_snapshot(payload)
        now_epoch = _now_epoch()

        if primary and primary.used_percent is not None:
            await self._usage_repo.add_entry(
                account_id=account.id,
                used_percent=float(primary.used_percent),
                input_tokens=None,
                output_tokens=None,
                window="primary",
                reset_at=_reset_at(primary.reset_at, primary.reset_after_seconds, now_epoch),
                window_minutes=_window_minutes(primary.limit_window_seconds),
                credits_has=credits_has,
                credits_unlimited=credits_unlimited,
                credits_balance=credits_balance,
                commit=False,
            )

        if secondary and secondary.used_percent is not None:
            await self._usage_repo.add_entry(
                account_id=account.id,
                used_percent=float(secondary.used_percent),
                input_tokens=None,
                output_tokens=None,
                window="secondary",
                reset_at=_reset_at(secondary.reset_at, secondary.reset_after_seconds, now_epoch),
                window_minutes=_window_minutes(secondary.limit_window_seconds),
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
                    deactivate_error=None,
                )
            except UsageFetchError as exc:
                if _should_deactivate_for_usage_error(exc.status_code):
                    return _UsageFetchResult(
                        account_id=target.account_id,
                        payload=None,
                        needs_auth_refresh=False,
                        deactivate_error=exc,
                    )
                if exc.status_code == 401 and self._auth_manager is not None:
                    return _UsageFetchResult(
                        account_id=target.account_id,
                        payload=None,
                        needs_auth_refresh=True,
                        deactivate_error=None,
                    )
                return _UsageFetchResult(
                    account_id=target.account_id,
                    payload=None,
                    needs_auth_refresh=False,
                    deactivate_error=None,
                )

    async def _deactivate_for_client_error(self, account: Account, exc: UsageFetchError) -> None:
        if not self._auth_manager:
            return
        reason = f"Usage API error: HTTP {exc.status_code} - {exc.message}"
        logger.warning(
            "Deactivating account due to client error account_id=%s status=%s message=%s request_id=%s",
            account.id,
            exc.status_code,
            exc.message,
            get_request_id(),
        )
        await self._auth_manager._repo.update_status(account.id, AccountStatus.DEACTIVATED, reason)
        account.status = AccountStatus.DEACTIVATED
        account.deactivation_reason = reason
        invalidate_accounts_list_cache()


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


_DEACTIVATING_USAGE_STATUS_CODES = {402, 403, 404}


def _should_deactivate_for_usage_error(status_code: int) -> bool:
    return status_code in _DEACTIVATING_USAGE_STATUS_CODES


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
    deactivate_error: UsageFetchError | None
