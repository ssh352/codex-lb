from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request

from app.dependencies import SettingsContext, get_settings_context
from app.modules.settings.schemas import DashboardSettingsResponse, DashboardSettingsUpdateRequest
from app.modules.settings.service import DashboardSettingsUpdateData

router = APIRouter(prefix="/api/settings", tags=["dashboard"])


@router.get("", response_model=DashboardSettingsResponse)
async def get_settings(
    context: SettingsContext = Depends(get_settings_context),
) -> DashboardSettingsResponse:
    settings = await context.service.get_settings()
    return DashboardSettingsResponse(
        prefer_earlier_reset_accounts=settings.prefer_earlier_reset_accounts,
        pinned_account_ids=settings.pinned_account_ids,
    )


@router.put("", response_model=DashboardSettingsResponse)
async def update_settings(
    request: Request,
    payload: DashboardSettingsUpdateRequest = Body(...),
    context: SettingsContext = Depends(get_settings_context),
) -> DashboardSettingsResponse:
    updated = await context.service.update_settings(
        DashboardSettingsUpdateData(
            prefer_earlier_reset_accounts=payload.prefer_earlier_reset_accounts,
            pinned_account_ids=payload.pinned_account_ids,
        )
    )
    service = getattr(request.app.state, "proxy_service", None)
    if service is not None:
        try:
            service.invalidate_routing_snapshot()
        except Exception:
            pass
    return DashboardSettingsResponse(
        prefer_earlier_reset_accounts=updated.prefer_earlier_reset_accounts,
        pinned_account_ids=updated.pinned_account_ids,
    )
