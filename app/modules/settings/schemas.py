from __future__ import annotations

from app.modules.shared.schemas import DashboardModel


class DashboardSettingsResponse(DashboardModel):
    prefer_earlier_reset_accounts: bool


class DashboardSettingsUpdateRequest(DashboardModel):
    prefer_earlier_reset_accounts: bool
