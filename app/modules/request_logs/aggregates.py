from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestLogModelUsageAggregate:
    model: str
    input_tokens_sum: int
    output_tokens_sum: int
    cached_input_tokens_sum: int


@dataclass(frozen=True, slots=True)
class RequestLogsUsageAggregates:
    total_requests: int
    error_requests: int
    tokens_sum: int
    cached_input_tokens_sum: int
    top_error: str | None
    by_model: list[RequestLogModelUsageAggregate]


def empty_request_logs_usage_aggregates() -> RequestLogsUsageAggregates:
    return RequestLogsUsageAggregates(
        total_requests=0,
        error_requests=0,
        tokens_sum=0,
        cached_input_tokens_sum=0,
        top_error=None,
        by_model=[],
    )
