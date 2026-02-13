from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import (
    AccountsSessionLocal,
    SessionLocal,
    _safe_close,
    _safe_rollback,
    get_accounts_session,
    get_session,
)
from app.modules.accounts.data_repository import AccountsDataRepository
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.service import AccountsService
from app.modules.dashboard.repository import DashboardRepository
from app.modules.dashboard.service import DashboardService
from app.modules.oauth.service import OauthService
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.service import ProxyService
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.request_logs.service import RequestLogsService
from app.modules.settings.repository import SettingsRepository
from app.modules.settings.service import SettingsService
from app.modules.usage.repository import UsageRepository
from app.modules.usage.service import UsageService


@dataclass(slots=True)
class AccountsContext:
    main_session: AsyncSession
    accounts_session: AsyncSession
    repository: AccountsRepository
    service: AccountsService


@dataclass(slots=True)
class UsageContext:
    main_session: AsyncSession
    accounts_session: AsyncSession
    usage_repository: UsageRepository
    service: UsageService


@dataclass(slots=True)
class OauthContext:
    service: OauthService


@dataclass(slots=True)
class ProxyContext:
    service: ProxyService


@dataclass(slots=True)
class RequestLogsContext:
    session: AsyncSession
    repository: RequestLogsRepository
    service: RequestLogsService


@dataclass(slots=True)
class SettingsContext:
    session: AsyncSession
    repository: SettingsRepository
    service: SettingsService


@dataclass(slots=True)
class DashboardContext:
    main_session: AsyncSession
    accounts_session: AsyncSession
    repository: DashboardRepository
    service: DashboardService


def get_accounts_context(
    accounts_session: AsyncSession = Depends(get_accounts_session),
    session: AsyncSession = Depends(get_session),
) -> AccountsContext:
    repository = AccountsRepository(accounts_session)
    data_repository = AccountsDataRepository(session)
    usage_repository = UsageRepository(session)
    settings_repository = SettingsRepository(session)
    service = AccountsService(repository, data_repository, usage_repository, settings_repository)
    return AccountsContext(
        main_session=session,
        accounts_session=accounts_session,
        repository=repository,
        service=service,
    )


def get_usage_context(
    accounts_session: AsyncSession = Depends(get_accounts_session),
    session: AsyncSession = Depends(get_session),
) -> UsageContext:
    usage_repository = UsageRepository(session)
    request_logs_repository = RequestLogsRepository(session)
    accounts_repository = AccountsRepository(accounts_session)
    service = UsageService(
        usage_repository,
        request_logs_repository,
        accounts_repository,
    )
    return UsageContext(
        main_session=session,
        accounts_session=accounts_session,
        usage_repository=usage_repository,
        service=service,
    )


@asynccontextmanager
async def _accounts_repo_context() -> AsyncIterator[AccountsRepository]:
    session = AccountsSessionLocal()
    try:
        yield AccountsRepository(session)
    except BaseException:
        await _safe_rollback(session)
        raise
    finally:
        if session.in_transaction():
            await _safe_rollback(session)
        await _safe_close(session)


@asynccontextmanager
async def _proxy_repo_context() -> AsyncIterator[ProxyRepositories]:
    main_session = SessionLocal()
    accounts_session = AccountsSessionLocal()
    try:
        yield ProxyRepositories(
            accounts=AccountsRepository(accounts_session),
            usage=UsageRepository(main_session),
            request_logs=RequestLogsRepository(main_session),
            sticky_sessions=StickySessionsRepository(main_session),
            settings=SettingsRepository(main_session),
        )
    except BaseException:
        await _safe_rollback(main_session)
        await _safe_rollback(accounts_session)
        raise
    finally:
        if main_session.in_transaction():
            await _safe_rollback(main_session)
        if accounts_session.in_transaction():
            await _safe_rollback(accounts_session)
        await _safe_close(main_session)
        await _safe_close(accounts_session)


def get_oauth_context(
    session: AsyncSession = Depends(get_accounts_session),
) -> OauthContext:
    accounts_repository = AccountsRepository(session)
    return OauthContext(service=OauthService(accounts_repository, repo_factory=_accounts_repo_context))


def get_proxy_context(request: Request) -> ProxyContext:
    service = getattr(request.app.state, "proxy_service", None)
    if service is None:
        service = ProxyService(repo_factory=_proxy_repo_context)
        request.app.state.proxy_service = service
    return ProxyContext(service=service)


def get_request_logs_context(
    accounts_session: AsyncSession = Depends(get_accounts_session),
    session: AsyncSession = Depends(get_session),
) -> RequestLogsContext:
    repository = RequestLogsRepository(session)
    accounts_repository = AccountsRepository(accounts_session)
    service = RequestLogsService(repository, accounts_repo=accounts_repository)
    return RequestLogsContext(session=session, repository=repository, service=service)


def get_settings_context(
    session: AsyncSession = Depends(get_session),
) -> SettingsContext:
    repository = SettingsRepository(session)
    service = SettingsService(repository)
    return SettingsContext(session=session, repository=repository, service=service)


def get_dashboard_context(
    accounts_session: AsyncSession = Depends(get_accounts_session),
    session: AsyncSession = Depends(get_session),
) -> DashboardContext:
    repository = DashboardRepository(main_session=session, accounts_session=accounts_session)
    service = DashboardService(repository)
    return DashboardContext(
        main_session=session,
        accounts_session=accounts_session,
        repository=repository,
        service=service,
    )
