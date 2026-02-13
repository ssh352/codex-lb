from __future__ import annotations

from datetime import timedelta

from app.core import usage as usage_core
from app.core.crypto import TokenEncryptor
from app.core.usage.types import UsageWindowRow
from app.core.usage.waste_pacing import (
    SecondaryWastePacingInput,
    compute_secondary_waste_pacing,
)
from app.core.utils.time import from_epoch_seconds, to_epoch_seconds_assuming_utc, utcnow
from app.db.models import UsageHistory
from app.modules.accounts.mappers import build_account_summaries
from app.modules.dashboard.repository import DashboardRepository
from app.modules.dashboard.schemas import (
    DashboardOverviewResponse,
    DashboardUsageWindows,
    DashboardWastePacing,
    DashboardWastePacingAccount,
    DashboardWastePacingSummary,
)
from app.modules.request_logs.mappers import to_request_log_entry
from app.modules.usage.builders import (
    build_usage_summary_response,
    build_usage_window_response,
)


class DashboardService:
    def __init__(self, repo: DashboardRepository) -> None:
        self._repo = repo
        self._encryptor = TokenEncryptor()

    async def get_overview(self, *, request_limit: int, request_offset: int) -> DashboardOverviewResponse:
        now = utcnow()
        now_epoch = to_epoch_seconds_assuming_utc(now)
        accounts = await self._repo.list_accounts()
        primary_usage = await self._repo.latest_usage_by_account("primary")
        secondary_usage = await self._repo.latest_usage_by_account("secondary")
        pinned_account_ids = set(await self._repo.pinned_account_ids())

        account_summaries = build_account_summaries(
            accounts=accounts,
            primary_usage=primary_usage,
            secondary_usage=secondary_usage,
            encryptor=self._encryptor,
            pinned_account_ids=pinned_account_ids,
        )

        primary_rows = _rows_from_latest(primary_usage)
        secondary_rows = _rows_from_latest(secondary_usage)

        secondary_minutes = await self._repo.latest_window_minutes("secondary")
        if secondary_minutes is None:
            secondary_minutes = usage_core.default_window_minutes("secondary")
        logs_secondary = []
        if secondary_minutes:
            logs_secondary = await self._repo.list_logs_since(now - timedelta(minutes=secondary_minutes))

        summary = build_usage_summary_response(
            accounts=accounts,
            primary_rows=primary_rows,
            secondary_rows=secondary_rows,
            logs_secondary=logs_secondary,
        )

        primary_window_minutes = await self._repo.latest_window_minutes("primary")
        if primary_window_minutes is None:
            primary_window_minutes = usage_core.default_window_minutes("primary")

        windows = DashboardUsageWindows(
            primary=build_usage_window_response(
                window_key="primary",
                window_minutes=primary_window_minutes,
                usage_rows=primary_rows,
                accounts=accounts,
            ),
            secondary=build_usage_window_response(
                window_key="secondary",
                window_minutes=secondary_minutes,
                usage_rows=secondary_rows,
                accounts=accounts,
            ),
        )

        recent_logs = await self._repo.list_recent_logs(limit=request_limit, offset=request_offset)
        request_logs = [to_request_log_entry(log) for log in recent_logs]

        waste_inputs: list[SecondaryWastePacingInput] = []
        for account in accounts:
            secondary_entry = secondary_usage.get(account.id)
            waste_inputs.append(
                SecondaryWastePacingInput(
                    account_id=account.id,
                    plan_type=account.plan_type,
                    secondary_used_percent=secondary_entry.used_percent if secondary_entry else None,
                    secondary_reset_at_epoch=secondary_entry.reset_at if secondary_entry else None,
                    secondary_window_minutes=secondary_entry.window_minutes if secondary_entry else None,
                )
            )
        waste_result = compute_secondary_waste_pacing(waste_inputs, now_epoch=now_epoch)
        waste_pacing = DashboardWastePacing(
            summary=DashboardWastePacingSummary(
                computed_at=now,
                accounts_evaluated=waste_result.summary.accounts_evaluated,
                accounts_at_risk=waste_result.summary.accounts_at_risk,
                projected_waste_credits_total=waste_result.summary.projected_waste_credits_total,
                current_rate_credits_per_hour_total=waste_result.summary.current_rate_credits_per_hour_total,
                required_rate_credits_per_hour_total=waste_result.summary.required_rate_credits_per_hour_total,
            ),
            accounts=[
                DashboardWastePacingAccount(
                    account_id=entry.account_id,
                    reset_at_secondary=from_epoch_seconds(entry.secondary_reset_at_epoch),
                    remaining_credits_secondary=entry.remaining_credits_secondary,
                    current_rate_credits_per_hour=entry.current_rate_credits_per_hour,
                    required_rate_credits_per_hour=entry.required_rate_credits_per_hour,
                    projected_waste_credits=entry.projected_waste_credits,
                    on_track=entry.on_track,
                )
                for entry in waste_result.accounts
            ],
        )

        return DashboardOverviewResponse(
            last_sync_at=_latest_recorded_at(primary_usage, secondary_usage),
            accounts=account_summaries,
            summary=summary,
            windows=windows,
            waste_pacing=waste_pacing,
            request_logs=request_logs,
        )


def _rows_from_latest(latest: dict[str, UsageHistory]) -> list[UsageWindowRow]:
    return [
        UsageWindowRow(
            account_id=entry.account_id,
            used_percent=entry.used_percent,
            reset_at=entry.reset_at,
            window_minutes=entry.window_minutes,
        )
        for entry in latest.values()
    ]


def _latest_recorded_at(
    primary_usage: dict[str, UsageHistory],
    secondary_usage: dict[str, UsageHistory],
):
    timestamps = [
        entry.recorded_at
        for entry in list(primary_usage.values()) + list(secondary_usage.values())
        if entry.recorded_at is not None
    ]
    return max(timestamps) if timestamps else None
