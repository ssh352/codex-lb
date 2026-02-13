from __future__ import annotations

from datetime import datetime
from typing import List

from pydantic import Field

from app.modules.accounts.schemas import AccountSummary
from app.modules.request_logs.schemas import RequestLogEntry
from app.modules.shared.schemas import DashboardModel
from app.modules.usage.schemas import UsageSummaryResponse, UsageWindowResponse


class DashboardUsageWindows(DashboardModel):
    primary: UsageWindowResponse
    secondary: UsageWindowResponse | None = None


class DashboardWastePacingAccount(DashboardModel):
    account_id: str
    reset_at_secondary: datetime | None = None
    remaining_credits_secondary: float | None = None
    current_rate_credits_per_hour: float | None = None
    required_rate_credits_per_hour: float | None = None
    projected_waste_credits: float | None = None
    on_track: bool | None = None


class DashboardWastePacingSummary(DashboardModel):
    computed_at: datetime
    accounts_evaluated: int
    accounts_at_risk: int
    projected_waste_credits_total: float
    current_rate_credits_per_hour_total: float | None = None
    required_rate_credits_per_hour_total: float | None = None


class DashboardWastePacing(DashboardModel):
    summary: DashboardWastePacingSummary
    accounts: List[DashboardWastePacingAccount] = Field(default_factory=list)


class DashboardOverviewResponse(DashboardModel):
    last_sync_at: datetime | None = None
    accounts: List[AccountSummary] = Field(default_factory=list)
    summary: UsageSummaryResponse
    windows: DashboardUsageWindows
    waste_pacing: DashboardWastePacing | None = None
    request_logs: List[RequestLogEntry] = Field(default_factory=list)
