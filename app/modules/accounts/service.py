from __future__ import annotations

from app.core.auth import (
    DEFAULT_EMAIL,
    DEFAULT_PLAN,
    claims_from_auth,
    generate_unique_account_id,
    parse_auth_json,
)
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.utils.time import to_epoch_seconds_assuming_utc, to_utc_naive, utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts.data_repository import AccountsDataRepository
from app.modules.accounts.list_cache import (
    get_or_build_accounts_list,
    invalidate_accounts_list_cache,
)
from app.modules.accounts.mappers import build_account_summaries
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.schemas import (
    AccountImportResponse,
    AccountSummary,
)
from app.modules.accounts.status_reconcile import stale_blocked_account_ids
from app.modules.settings.repository import SettingsRepository
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import UsageUpdater


class AccountsService:
    def __init__(
        self,
        repo: AccountsRepository,
        data_repo: AccountsDataRepository | None = None,
        usage_repo: UsageRepository | None = None,
        settings_repo: SettingsRepository | None = None,
    ) -> None:
        self._repo = repo
        self._data_repo = data_repo
        self._usage_repo = usage_repo
        self._settings_repo = settings_repo
        self._usage_updater = UsageUpdater(usage_repo, repo) if usage_repo else None
        self._encryptor = TokenEncryptor()

    @classmethod
    def invalidate_cache(cls) -> None:
        invalidate_accounts_list_cache()

    async def list_accounts(self) -> list[AccountSummary]:
        async def _build() -> list[AccountSummary]:
            accounts = await self._repo.list_accounts()
            if not accounts:
                return []
            if self._usage_repo:
                primary_usage, secondary_usage = await self._usage_repo.latest_primary_secondary_by_account()
            else:
                primary_usage = {}
                secondary_usage = {}

            now_epoch = to_epoch_seconds_assuming_utc(utcnow())
            stale_ids = stale_blocked_account_ids(
                accounts=accounts,
                primary_usage=primary_usage,
                secondary_usage=secondary_usage,
                now_epoch=now_epoch,
            )
            if stale_ids:
                await self._repo.bulk_set_active(sorted(stale_ids))
                for account in accounts:
                    if account.id in stale_ids:
                        account.status = AccountStatus.ACTIVE
                        account.deactivation_reason = None
                        account.reset_at = None

            # Data hygiene: `accounts.reset_at` is a persisted "blocked until" hint. If the account
            # is not in a blocked status, any stored reset timestamp is stale/inconsistent and
            # should be cleared. This can happen if earlier versions persisted a reset hint without
            # also persisting a blocked status.
            inconsistent_ids = {
                account.id
                for account in accounts
                if account.reset_at is not None
                and account.status not in (AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED)
            }
            if inconsistent_ids:
                await self._repo.bulk_clear_reset_at(sorted(inconsistent_ids))
                for account in accounts:
                    if account.id in inconsistent_ids:
                        account.reset_at = None

            pinned_ids = set(await self._settings_repo.pinned_account_ids()) if self._settings_repo else set()
            return build_account_summaries(
                accounts=accounts,
                primary_usage=primary_usage,
                secondary_usage=secondary_usage,
                encryptor=self._encryptor,
                pinned_account_ids=pinned_ids,
            )

        return await get_or_build_accounts_list(_build)

    async def import_account(self, raw: bytes) -> AccountImportResponse:
        auth = parse_auth_json(raw)
        claims = claims_from_auth(auth)

        email = claims.email or DEFAULT_EMAIL
        raw_account_id = claims.account_id
        account_id = generate_unique_account_id(raw_account_id, email)
        plan_type = coerce_account_plan_type(claims.plan_type, DEFAULT_PLAN)
        last_refresh = to_utc_naive(auth.last_refresh_at) if auth.last_refresh_at else utcnow()

        account = Account(
            id=account_id,
            chatgpt_account_id=raw_account_id,
            email=email,
            plan_type=plan_type,
            access_token_encrypted=self._encryptor.encrypt(auth.tokens.access_token),
            refresh_token_encrypted=self._encryptor.encrypt(auth.tokens.refresh_token),
            id_token_encrypted=self._encryptor.encrypt(auth.tokens.id_token),
            last_refresh=last_refresh,
            status=AccountStatus.ACTIVE,
            deactivation_reason=None,
        )

        saved = await self._repo.upsert(account)
        type(self).invalidate_cache()
        if self._usage_repo and self._usage_updater:
            latest_usage = await self._usage_repo.latest_by_account(window="primary")
            await self._usage_updater.refresh_accounts([saved], latest_usage)
        return AccountImportResponse(
            account_id=saved.id,
            email=saved.email,
            plan_type=saved.plan_type,
            status=saved.status,
        )

    async def reactivate_account(self, account_id: str) -> bool:
        success = await self._repo.update_status(account_id, AccountStatus.ACTIVE, None)
        if success:
            type(self).invalidate_cache()
        return success

    async def pause_account(self, account_id: str) -> bool:
        success = await self._repo.update_status(account_id, AccountStatus.PAUSED, None)
        if success:
            type(self).invalidate_cache()
        return success

    async def delete_account(self, account_id: str) -> bool:
        if self._data_repo is not None:
            await self._data_repo.delete_account_data(account_id)
        success = await self._repo.delete(account_id)
        if success:
            type(self).invalidate_cache()
        return success

    async def pin_account(self, account_id: str) -> list[str] | None:
        if self._settings_repo is None:
            raise RuntimeError("AccountsService pin_account requires settings_repo")
        existing = await self._repo.get_account(account_id)
        if existing is None:
            return None
        pinned = await self._settings_repo.pinned_account_ids()
        if account_id not in pinned:
            pinned = [*pinned, account_id]
            await self._settings_repo.update(pinned_account_ids=pinned)
            type(self).invalidate_cache()
        return await self._settings_repo.pinned_account_ids()

    async def unpin_account(self, account_id: str) -> list[str] | None:
        if self._settings_repo is None:
            raise RuntimeError("AccountsService unpin_account requires settings_repo")
        existing = await self._repo.get_account(account_id)
        if existing is None:
            return None
        pinned = await self._settings_repo.pinned_account_ids()
        if account_id in pinned:
            pinned = [entry for entry in pinned if entry != account_id]
            await self._settings_repo.update(pinned_account_ids=pinned)
            type(self).invalidate_cache()
        return await self._settings_repo.pinned_account_ids()
