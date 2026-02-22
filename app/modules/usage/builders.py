from __future__ import annotations

from app.core import usage as usage_core
from app.core.usage.pricing import CostItem, UsageTokens, calculate_costs
from app.core.usage.types import (
    UsageCostSummary,
    UsageMetricsSummary,
    UsageSummaryPayload,
    UsageWindowRow,
    UsageWindowSnapshot,
)
from app.core.utils.time import from_epoch_seconds
from app.db.models import Account
from app.modules.request_logs.aggregates import RequestLogsUsageAggregates
from app.modules.usage.schemas import (
    UsageCost,
    UsageCostByModel,
    UsageHistoryItem,
    UsageHistoryResponse,
    UsageMetrics,
    UsageSummaryResponse,
    UsageWindow,
    UsageWindowResponse,
)


def build_usage_summary_response(
    *,
    accounts: list[Account],
    primary_rows: list[UsageWindowRow],
    secondary_rows: list[UsageWindowRow],
    logs_secondary: RequestLogsUsageAggregates,
) -> UsageSummaryResponse:
    account_map = {account.id: account for account in accounts}
    primary_window = usage_core.summarize_usage_window(primary_rows, account_map, "primary")
    secondary_window = usage_core.summarize_usage_window(secondary_rows, account_map, "secondary")

    cost_items = [
        CostItem(
            model=entry.model,
            usage=UsageTokens(
                input_tokens=float(entry.input_tokens_sum),
                output_tokens=float(entry.output_tokens_sum),
                cached_input_tokens=float(entry.cached_input_tokens_sum),
            ),
        )
        for entry in logs_secondary.by_model
        if entry.model
    ]
    cost = calculate_costs(cost_items)
    metrics = _usage_metrics(logs_secondary)

    payload = usage_core.parse_usage_summary(primary_window, secondary_window, cost, metrics)
    return _summary_payload_to_response(payload)


def build_usage_history_response(
    *,
    hours: int,
    usage_rows: list[UsageWindowRow],
    accounts: list[Account],
    window: str,
) -> UsageHistoryResponse:
    account_map = {account.id: account for account in accounts}
    accounts_history = _build_account_history(usage_rows, account_map, window)
    return UsageHistoryResponse(window_hours=hours, accounts=accounts_history)


def build_usage_window_response(
    *,
    window_key: str,
    window_minutes: int | None,
    usage_rows: list[UsageWindowRow],
    accounts: list[Account],
) -> UsageWindowResponse:
    account_map = {account.id: account for account in accounts}
    accounts_history = _build_account_history(usage_rows, account_map, window_key)
    return UsageWindowResponse(
        window_key=window_key,
        window_minutes=window_minutes,
        accounts=accounts_history,
    )


def _build_account_history(
    usage_rows: list[UsageWindowRow],
    account_map: dict[str, Account],
    window: str,
) -> list[UsageHistoryItem]:
    usage_by_account = {row.account_id: row for row in usage_rows}

    results: list[UsageHistoryItem] = []
    for account_id, account in account_map.items():
        usage = usage_by_account.get(account_id)
        used_percent = usage.used_percent if usage else None
        used_percent_value = float(used_percent) if used_percent is not None else 0.0
        remaining_percent = usage_core.remaining_percent_from_used(used_percent_value) or 0.0
        capacity = usage_core.capacity_for_plan(account.plan_type, window)
        remaining_credits = usage_core.remaining_credits_from_percent(used_percent_value, capacity) or 0.0
        results.append(
            UsageHistoryItem(
                account_id=account_id,
                email=account.email,
                remaining_percent_avg=remaining_percent,
                capacity_credits=float(capacity or 0.0),
                remaining_credits=float(remaining_credits),
            )
        )
    return results


def _usage_metrics(logs_secondary: RequestLogsUsageAggregates) -> UsageMetricsSummary:
    total_requests = logs_secondary.total_requests
    error_requests = logs_secondary.error_requests
    error_rate: float | None = None
    if total_requests > 0:
        error_rate = error_requests / total_requests
    top_error = logs_secondary.top_error
    tokens_secondary = logs_secondary.tokens_sum
    cached_tokens_secondary = logs_secondary.cached_input_tokens_sum
    return UsageMetricsSummary(
        requests_7d=total_requests,
        tokens_secondary_window=tokens_secondary,
        cached_tokens_secondary_window=cached_tokens_secondary,
        error_rate_7d=error_rate,
        top_error=top_error,
    )


def _summary_payload_to_response(payload: UsageSummaryPayload) -> UsageSummaryResponse:
    return UsageSummaryResponse(
        primary_window=_window_snapshot_to_model(payload.primary_window),
        secondary_window=_window_snapshot_to_model(payload.secondary_window) if payload.secondary_window else None,
        cost=_cost_summary_to_model(payload.cost),
        metrics=_metrics_summary_to_model(payload.metrics) if payload.metrics else None,
    )


def _window_snapshot_to_model(snapshot: UsageWindowSnapshot) -> UsageWindow:
    capacity_credits = float(snapshot.capacity_credits)
    remaining_credits = usage_core.remaining_credits_from_used(snapshot.used_credits, capacity_credits) or 0.0
    remaining_percent = max(0.0, 100.0 - float(snapshot.used_percent)) if capacity_credits > 0 else 0.0
    return UsageWindow(
        remaining_percent=remaining_percent,
        capacity_credits=capacity_credits,
        remaining_credits=remaining_credits,
        reset_at=from_epoch_seconds(snapshot.reset_at),
        window_minutes=snapshot.window_minutes,
    )


def _cost_summary_to_model(cost: UsageCostSummary) -> UsageCost:
    return UsageCost(
        currency=cost.currency,
        totalUsd7d=cost.total_usd_7d,
        by_model=[UsageCostByModel(model=item.model, usd=item.usd) for item in cost.by_model],
    )


def _metrics_summary_to_model(metrics: UsageMetricsSummary) -> UsageMetrics:
    return UsageMetrics(
        requests7d=metrics.requests_7d,
        tokens_secondary_window=metrics.tokens_secondary_window,
        cached_tokens_secondary_window=metrics.cached_tokens_secondary_window,
        errorRate7d=metrics.error_rate_7d,
        top_error=metrics.top_error,
    )
