from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestLogModelOption:
    model: str
    reasoning_effort: str | None


@dataclass(frozen=True, slots=True)
class RequestLogStatusFilter:
    include_success: bool
    include_error_other: bool
    error_codes_in: list[str] | None
    error_codes_excluding: list[str] | None


@dataclass(frozen=True, slots=True)
class RequestLogFilterOptions:
    account_ids: list[str]
    model_options: list[RequestLogModelOption]
