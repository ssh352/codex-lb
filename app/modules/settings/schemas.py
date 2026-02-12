from __future__ import annotations

from app.modules.shared.schemas import DashboardModel


class DashboardSettingsResponse(DashboardModel):
    totp_required_on_login: bool
    totp_configured: bool


class DashboardSettingsUpdateRequest(DashboardModel):
    totp_required_on_login: bool | None = None
