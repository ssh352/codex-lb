from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import dashboard_error
from app.core.utils.time import from_epoch_seconds
from app.db.models import AccountStatus
from app.dependencies import AccountsContext, ProxyContext, get_accounts_context, get_proxy_context
from app.modules.accounts.schemas import (
    AccountDeleteResponse,
    AccountImportResponse,
    AccountPauseResponse,
    AccountPinResponse,
    AccountReactivateProbe,
    AccountReactivateResponse,
    AccountsResponse,
)
from app.modules.proxy.service import ProbeResult
from app.modules.request_logs.repository import RequestLogsRepository

router = APIRouter(prefix="/api/accounts", tags=["dashboard"])


def _invalidate_proxy_routing_snapshot(request: Request) -> None:
    service = getattr(request.app.state, "proxy_service", None)
    if service is None:
        return
    try:
        service.invalidate_routing_snapshot()
    except Exception:
        return


async def _latest_success_model_for_account(session: AsyncSession, *, account_id: str) -> str | None:
    repo = RequestLogsRepository(session)
    rows = await repo.list_recent(
        limit=1,
        account_ids=[account_id],
        include_success=True,
        include_error_other=False,
    )
    if not rows:
        return None
    model = (rows[0].model or "").strip()
    return model or None


def _probe_failure_message(
    *,
    status_code: int,
    error_type: str | None,
    error_code: str | None,
    error_message: str | None,
) -> str:
    parts = []
    if error_message:
        parts.append(error_message)
    if error_code:
        parts.append(f"code={error_code}")
    if error_type:
        parts.append(f"type={error_type}")
    parts.append(f"http={status_code}")
    return "Probe failed: " + " ".join(parts) if parts else f"Probe failed (http={status_code})"


def _iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    value = dt.isoformat()
    return value.replace("+00:00", "Z")


def _blocked_status_for_probe_error(probe: ProbeResult) -> AccountStatus | None:
    code = (probe.error_code or probe.error_type or "").strip().lower()
    if not code:
        return None
    if code in ("rate_limit_exceeded", "usage_limit_reached"):
        return AccountStatus.RATE_LIMITED
    if code in ("insufficient_quota", "usage_not_included", "quota_exceeded"):
        return AccountStatus.QUOTA_EXCEEDED
    return None


@router.get("", response_model=AccountsResponse)
async def list_accounts(
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountsResponse:
    accounts = await context.service.list_accounts()
    return AccountsResponse(accounts=accounts)


@router.post("/import", response_model=AccountImportResponse)
async def import_account(
    request: Request,
    auth_json: UploadFile = File(...),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountImportResponse | JSONResponse:
    raw = await auth_json.read()
    try:
        result = await context.service.import_account(raw)
        _invalidate_proxy_routing_snapshot(request)
        return result
    except Exception:
        return JSONResponse(
            status_code=400,
            content=dashboard_error("invalid_auth_json", "Invalid auth.json payload"),
        )


@router.post("/{account_id}/reactivate", response_model=AccountReactivateResponse)
async def reactivate_account(
    request: Request,
    account_id: str,
    context: AccountsContext = Depends(get_accounts_context),
    proxy_context: ProxyContext = Depends(get_proxy_context),
) -> AccountReactivateResponse | JSONResponse:
    existing = await context.service.get_account(account_id)
    if existing is None:
        return JSONResponse(
            status_code=404,
            content=dashboard_error("account_not_found", "Account not found"),
        )

    if existing.status == AccountStatus.DEACTIVATED:
        return JSONResponse(
            status_code=409,
            content=dashboard_error("account_deactivated", "Account requires re-authentication"),
        )

    probe_model = (
        await _latest_success_model_for_account(context.main_session, account_id=account_id) or "gpt-5.3-codex"
    )
    probe = await proxy_context.service.probe_compact_responses(account_id=account_id, model=probe_model)
    if not probe.ok:
        resets_at = from_epoch_seconds(probe.resets_at) if probe.resets_at is not None else None
        failure = {
            "code": "reactivate_probe_failed",
            "message": _probe_failure_message(
                status_code=probe.status_code,
                error_type=probe.error_type,
                error_code=probe.error_code,
                error_message=probe.error_message,
            ),
            "details": {
                "probeModel": probe_model,
                "upstreamStatusCode": probe.status_code,
                "upstreamErrorType": probe.error_type,
                "upstreamErrorCode": probe.error_code,
                "upstreamErrorMessage": probe.error_message,
                "resetsAt": _iso_utc(resets_at),
                "resetsInSeconds": probe.resets_in_seconds,
            },
        }
        return JSONResponse(status_code=409, content={"error": failure})

    success = await context.service.reactivate_account(account_id)
    if not success:
        return JSONResponse(
            status_code=404,
            content=dashboard_error("account_not_found", "Account not found"),
        )
    proxy_context.service.reset_account_runtime_state(account_id)
    _invalidate_proxy_routing_snapshot(request)
    return AccountReactivateResponse(
        status="reactivated",
        probe=AccountReactivateProbe(
            ok=True,
            status_code=probe.status_code,
        ),
    )


@router.post("/{account_id}/pause", response_model=AccountPauseResponse)
async def pause_account(
    request: Request,
    account_id: str,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountPauseResponse | JSONResponse:
    success = await context.service.pause_account(account_id)
    if not success:
        return JSONResponse(
            status_code=404,
            content=dashboard_error("account_not_found", "Account not found"),
        )
    _invalidate_proxy_routing_snapshot(request)
    return AccountPauseResponse(status="paused")


@router.delete("/{account_id}", response_model=AccountDeleteResponse)
async def delete_account(
    request: Request,
    account_id: str,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountDeleteResponse | JSONResponse:
    success = await context.service.delete_account(account_id)
    if not success:
        return JSONResponse(
            status_code=404,
            content=dashboard_error("account_not_found", "Account not found"),
        )
    _invalidate_proxy_routing_snapshot(request)
    return AccountDeleteResponse(status="deleted")


@router.post("/{account_id}/pin", response_model=AccountPinResponse)
async def pin_account(
    request: Request,
    account_id: str,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountPinResponse | JSONResponse:
    pinned = await context.service.pin_account(account_id)
    if pinned is None:
        return JSONResponse(
            status_code=404,
            content=dashboard_error("account_not_found", "Account not found"),
        )
    _invalidate_proxy_routing_snapshot(request)
    return AccountPinResponse(status="pinned", pinned_account_ids=pinned)


@router.post("/{account_id}/unpin", response_model=AccountPinResponse)
async def unpin_account(
    request: Request,
    account_id: str,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountPinResponse | JSONResponse:
    pinned = await context.service.unpin_account(account_id)
    if pinned is None:
        return JSONResponse(
            status_code=404,
            content=dashboard_error("account_not_found", "Account not found"),
        )
    _invalidate_proxy_routing_snapshot(request)
    return AccountPinResponse(status="unpinned", pinned_account_ids=pinned)
