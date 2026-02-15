from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse

from app.core.errors import dashboard_error
from app.dependencies import AccountsContext, get_accounts_context
from app.modules.accounts.schemas import (
    AccountDeleteResponse,
    AccountImportResponse,
    AccountPauseResponse,
    AccountPinResponse,
    AccountReactivateResponse,
    AccountsResponse,
)

router = APIRouter(prefix="/api/accounts", tags=["dashboard"])


def _invalidate_proxy_routing_snapshot(request: Request) -> None:
    service = getattr(request.app.state, "proxy_service", None)
    if service is None:
        return
    try:
        service.invalidate_routing_snapshot()
    except Exception:
        return


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
) -> AccountReactivateResponse | JSONResponse:
    success = await context.service.reactivate_account(account_id)
    if not success:
        return JSONResponse(
            status_code=404,
            content=dashboard_error("account_not_found", "Account not found"),
        )
    _invalidate_proxy_routing_snapshot(request)
    return AccountReactivateResponse(status="reactivated")


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
