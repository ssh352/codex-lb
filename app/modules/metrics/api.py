from __future__ import annotations

import asyncio
import contextlib
import time

from fastapi import APIRouter
from starlette.responses import Response

from app.core.config.settings import get_settings
from app.core.metrics import get_metrics
from app.core.metrics.metrics import AccountIdentityObservation
from app.core.request_logs.buffer import get_request_log_buffer
from app.db.session import AccountsSessionLocal, _safe_close, _safe_rollback
from app.modules.accounts.repository import AccountsRepository

router = APIRouter(tags=["metrics"])

_IDENTITY_REFRESH_INTERVAL_SECONDS = 60.0
_identity_refresh_lock = asyncio.Lock()
_last_identity_refresh_monotonic: float = 0.0


async def _maybe_refresh_account_identity_gauges() -> None:
    global _last_identity_refresh_monotonic
    now = time.monotonic()
    if now - _last_identity_refresh_monotonic < _IDENTITY_REFRESH_INTERVAL_SECONDS:
        return
    async with _identity_refresh_lock:
        now = time.monotonic()
        if now - _last_identity_refresh_monotonic < _IDENTITY_REFRESH_INTERVAL_SECONDS:
            return

        accounts_session = AccountsSessionLocal()
        try:
            accounts_repo = AccountsRepository(accounts_session)
            accounts = await accounts_repo.list_accounts()
            get_metrics().refresh_account_identity_gauges(
                [AccountIdentityObservation(account_id=account.id, email=account.email) for account in accounts],
                mode=get_settings().metrics_account_identity_mode,
            )
            _last_identity_refresh_monotonic = now
        finally:
            if accounts_session.in_transaction():
                await _safe_rollback(accounts_session)
            with contextlib.suppress(Exception):
                await _safe_close(accounts_session)


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    metrics = get_metrics()
    metrics.set_request_log_buffer_size(get_request_log_buffer().size())
    await _maybe_refresh_account_identity_gauges()
    return Response(
        content=metrics.render(),
        media_type=metrics.content_type,
        headers={"Cache-Control": "no-cache"},
    )
