from __future__ import annotations

from app.modules.shared.schemas import DashboardModel


class DashboardSettingsResponse(DashboardModel):
    prefer_earlier_reset_accounts: bool
    totp_required_on_login: bool
    totp_configured: bool


class DashboardSettingsUpdateRequest(DashboardModel):
    prefer_earlier_reset_accounts: bool
    totp_required_on_login: bool | None = None
