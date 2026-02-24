from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Iterable, Literal, Sequence

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from app.core import usage as usage_core
from app.core.plan_types import canonicalize_account_plan_type
from app.core.usage.logs import cost_from_log
from app.core.usage.pricing import get_pricing_for_model
from app.core.usage.waste_pacing import SecondaryWastePacingInput

_PROM_CONTENT_TYPE: Final[str] = "text/plain; version=0.0.4; charset=utf-8"


@dataclass(slots=True)
class ProxyRequestObservation:
    account_id: str | None
    api: str
    status: str
    model: str | None
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    reasoning_tokens: int | None
    error_code: str | None


@dataclass(slots=True)
class SecondaryUsagePercentState:
    used_percent: float
    reset_at_epoch: int | None


@dataclass(frozen=True, slots=True)
class AccountIdentityObservation:
    account_id: str
    email: str
    plan_type: str


@dataclass(frozen=True, slots=True)
class SecondaryQuotaEstimateObservation:
    account_id: str
    cost_usd_7d: float
    used_delta_pp_7d: float


def _error_class(error_code: str | None) -> str:
    if not error_code:
        return "unknown"
    if error_code in {"rate_limit_exceeded", "usage_limit_reached"}:
        return "rate_limit"
    if error_code in {"insufficient_quota", "usage_not_included", "quota_exceeded"}:
        return "quota"
    if error_code in {"invalid_api_key", "invalid_auth", "auth_refresh_failed"} or error_code.startswith("auth_"):
        return "auth"
    if error_code in {"missing_prompt_cache_key", "invalid_request"} or error_code.startswith("invalid_"):
        return "invalid_request"
    if error_code.startswith("server_") or error_code.endswith("_server_error"):
        return "upstream"
    if error_code in {"no_accounts"}:
        return "internal"
    return "unknown"


def _normalize_account_status(value: str) -> str:
    match value:
        case "active" | "paused" | "deactivated":
            return value
        case "rate_limited":
            return "limited"
        case "quota_exceeded":
            return "exceeded"
        case _:
            return value


def _unpriced_success_reason(obs: ProxyRequestObservation) -> str:
    if not obs.model:
        return "missing_model"
    if obs.input_tokens is None:
        return "missing_usage"
    output_tokens = obs.output_tokens if obs.output_tokens is not None else obs.reasoning_tokens
    if output_tokens is None:
        return "missing_usage"
    if get_pricing_for_model(obs.model, None, None) is None:
        return "unknown_pricing"
    return "unknown"


class Metrics:
    def __init__(self, *, registry: CollectorRegistry | None = None) -> None:
        self._registry = registry or CollectorRegistry(auto_describe=True)
        self._known_account_ids: set[str] = set()
        self._secondary_usage_state: dict[str, SecondaryUsagePercentState] = {}
        self._known_account_identity_ids: set[str] = set()
        self._account_identity_state: dict[str, tuple[str, str]] = {}
        self._known_secondary_quota_ids: set[str] = set()

        self._proxy_requests_total = Counter(
            "codex_lb_proxy_requests_total",
            "Total proxy requests completed.",
            labelnames=("status", "model", "api"),
            registry=self._registry,
        )
        self._proxy_errors_total = Counter(
            "codex_lb_proxy_errors_total",
            "Total proxy errors by normalized error code.",
            labelnames=("error_code",),
            registry=self._registry,
        )
        self._proxy_latency_ms = Histogram(
            "codex_lb_proxy_latency_ms",
            "Proxy request latency in milliseconds.",
            labelnames=("model", "api"),
            # 25ms .. 15m
            buckets=(
                25,
                50,
                100,
                250,
                500,
                1_000,
                2_000,
                5_000,
                10_000,
                30_000,
                60_000,
                75_000,
                90_000,
                120_000,
                150_000,
                180_000,
                240_000,
                300_000,
                420_000,
                600_000,
                900_000,
            ),
            registry=self._registry,
        )
        self._proxy_tokens_total = Counter(
            "codex_lb_proxy_tokens_total",
            "Total tokens by kind and model.",
            labelnames=("kind", "model"),
            registry=self._registry,
        )
        self._proxy_cost_usd_total = Counter(
            "codex_lb_proxy_cost_usd_total",
            "Total estimated cost (USD) by model.",
            labelnames=("model",),
            registry=self._registry,
        )

        self._proxy_account_requests_total = Counter(
            "codex_lb_proxy_account_requests_total",
            "Total proxy requests completed by account.",
            labelnames=("account_id", "status", "api"),
            registry=self._registry,
        )
        self._proxy_account_tokens_total = Counter(
            "codex_lb_proxy_account_tokens_total",
            "Total tokens by account, kind, and API.",
            labelnames=("account_id", "kind", "api"),
            registry=self._registry,
        )
        self._proxy_account_cost_usd_total = Counter(
            "codex_lb_proxy_account_cost_usd_total",
            "Total estimated cost (USD) by account and API.",
            labelnames=("account_id", "api"),
            registry=self._registry,
        )
        self._proxy_account_errors_total = Counter(
            "codex_lb_proxy_account_errors_total",
            "Total proxy errors by account and coarse error class.",
            labelnames=("account_id", "error_class"),
            registry=self._registry,
        )
        self._proxy_retries_total = Counter(
            "codex_lb_proxy_retries_total",
            "Total proxy retry attempts by API and coarse error class.",
            labelnames=("api", "error_class"),
            registry=self._registry,
        )
        self._proxy_account_retries_total = Counter(
            "codex_lb_proxy_account_retries_total",
            "Total proxy retry attempts by account, API and coarse error class.",
            labelnames=("account_id", "api", "error_class"),
            registry=self._registry,
        )
        self._proxy_unpriced_success_total = Counter(
            "codex_lb_proxy_unpriced_success_total",
            "Total successful proxy requests where cost could not be estimated.",
            labelnames=("api", "reason"),
            registry=self._registry,
        )
        self._proxy_account_unpriced_success_total = Counter(
            "codex_lb_proxy_account_unpriced_success_total",
            "Total successful proxy requests where cost could not be estimated by account.",
            labelnames=("account_id", "api", "reason"),
            registry=self._registry,
        )
        self._account_identity = Gauge(
            "codex_lb_account_identity",
            "Account identity labels for dashboards (display is configurable).",
            labelnames=("account_id", "display", "plan_type"),
            registry=self._registry,
        )

        self._lb_select_total = Counter(
            "codex_lb_lb_select_total",
            "Load balancer selection attempts.",
            labelnames=("pool", "sticky_backend", "reallocate_sticky", "outcome"),
            registry=self._registry,
        )
        self._lb_selected_tier_total = Counter(
            "codex_lb_lb_selected_tier_total",
            "Load balancer selected tier counts for successful selections.",
            labelnames=("pool", "sticky_backend", "reallocate_sticky", "tier"),
            registry=self._registry,
        )
        self._lb_tier_score = Histogram(
            "codex_lb_lb_tier_score",
            "Load balancer per-tier score values observed during selection.",
            labelnames=("pool", "sticky_backend", "reallocate_sticky", "tier"),
            buckets=(0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 50, 100),
            registry=self._registry,
        )
        self._lb_mark_total = Counter(
            "codex_lb_lb_mark_total",
            "Load balancer mark events by account.",
            labelnames=("event", "account_id"),
            registry=self._registry,
        )
        self._lb_mark_permanent_failure_total = Counter(
            "codex_lb_lb_mark_permanent_failure_total",
            "Load balancer permanent failures by code.",
            labelnames=("code",),
            registry=self._registry,
        )
        self._lb_snapshot_refresh_total = Counter(
            "codex_lb_lb_snapshot_refresh_total",
            "Total load balancer snapshot refreshes.",
            registry=self._registry,
        )
        self._lb_snapshot_updated_at_seconds = Gauge(
            "codex_lb_lb_snapshot_updated_at_seconds",
            "Unix seconds when the load balancer snapshot was last refreshed.",
            registry=self._registry,
        )
        self._usage_refresh_failures_total = Counter(
            "codex_lb_usage_refresh_failures_total",
            "Total usage refresh failures by status code and phase.",
            labelnames=("status_code", "phase"),
            registry=self._registry,
        )

        self._request_log_buffer_size = Gauge(
            "codex_lb_request_log_buffer_size",
            "Current request log buffer size.",
            registry=self._registry,
        )
        self._request_log_buffer_dropped_total = Counter(
            "codex_lb_request_log_buffer_dropped_total",
            "Total request logs dropped due to a full buffer.",
            registry=self._registry,
        )

        self._accounts_total = Gauge(
            "codex_lb_accounts_total",
            "Accounts by normalized status.",
            labelnames=("status",),
            registry=self._registry,
        )

        self._secondary_used_percent = Gauge(
            "codex_lb_secondary_used_percent",
            "Latest secondary used percent per account.",
            labelnames=("account_id",),
            registry=self._registry,
        )
        self._secondary_resets_total = Counter(
            "codex_lb_secondary_resets_total",
            "Count of detected secondary window resets per account (used percent decreases and reset_at advances).",
            labelnames=("account_id",),
            registry=self._registry,
        )
        self._secondary_reset_at_seconds = Gauge(
            "codex_lb_secondary_reset_at_seconds",
            "Secondary reset time (unix seconds) per account.",
            labelnames=("account_id",),
            registry=self._registry,
        )
        self._secondary_window_minutes = Gauge(
            "codex_lb_secondary_window_minutes",
            "Secondary window minutes per account.",
            labelnames=("account_id",),
            registry=self._registry,
        )
        self._secondary_remaining_credits = Gauge(
            "codex_lb_secondary_remaining_credits",
            "Estimated secondary remaining credits per account.",
            labelnames=("account_id",),
            registry=self._registry,
        )

        self._secondary_cost_usd_7d = Gauge(
            "codex_lb_proxy_account_cost_usd_7d",
            "Estimated proxy cost (USD) since the current secondary weekly cycle start, clipped to "
            "the last 7 days (SQLite-derived).",
            labelnames=("account_id",),
            registry=self._registry,
        )
        self._secondary_used_percent_delta_pp_7d = Gauge(
            "codex_lb_secondary_used_percent_delta_pp_7d",
            "Latest secondary used_percent (percentage points since reset start) for the current "
            "cycle, clipped to the last 7 days (SQLite-derived).",
            labelnames=("account_id",),
            registry=self._registry,
        )
        self._secondary_implied_quota_usd_7d = Gauge(
            "codex_lb_secondary_implied_quota_usd_7d",
            "Implied secondary quota (USD) = cost_usd_7d / (used_pp_7d/100).",
            labelnames=("account_id",),
            registry=self._registry,
        )

    @property
    def content_type(self) -> str:
        return _PROM_CONTENT_TYPE

    def render(self) -> bytes:
        return generate_latest(self._registry)

    def observe_proxy_request(self, obs: ProxyRequestObservation) -> None:
        api = obs.api or "unknown"
        status = obs.status or "unknown"
        model = obs.model or "unknown"

        self._proxy_requests_total.labels(status=status, model=model, api=api).inc()
        if obs.account_id:
            self._proxy_account_requests_total.labels(account_id=obs.account_id, status=status, api=api).inc()

        if obs.latency_ms is not None and obs.latency_ms >= 0:
            latency_ms = float(obs.latency_ms)
            self._proxy_latency_ms.labels(model=model, api=api).observe(latency_ms)

        if obs.error_code:
            self._proxy_errors_total.labels(error_code=obs.error_code).inc()
        if obs.account_id and status == "error":
            self._proxy_account_errors_total.labels(
                account_id=obs.account_id,
                error_class=_error_class(obs.error_code),
            ).inc()

        if obs.input_tokens is not None:
            self._proxy_tokens_total.labels(kind="input", model=model).inc(float(max(0, int(obs.input_tokens))))
            if obs.account_id:
                self._proxy_account_tokens_total.labels(account_id=obs.account_id, kind="input", api=api).inc(
                    float(max(0, int(obs.input_tokens)))
                )
        if obs.output_tokens is not None:
            self._proxy_tokens_total.labels(kind="output", model=model).inc(float(max(0, int(obs.output_tokens))))
            if obs.account_id:
                self._proxy_account_tokens_total.labels(account_id=obs.account_id, kind="output", api=api).inc(
                    float(max(0, int(obs.output_tokens)))
                )
        if obs.cached_input_tokens is not None:
            self._proxy_tokens_total.labels(kind="cached_input", model=model).inc(
                float(max(0, int(obs.cached_input_tokens)))
            )
            if obs.account_id:
                self._proxy_account_tokens_total.labels(account_id=obs.account_id, kind="cached_input", api=api).inc(
                    float(max(0, int(obs.cached_input_tokens)))
                )
        if obs.reasoning_tokens is not None:
            self._proxy_tokens_total.labels(kind="reasoning", model=model).inc(float(max(0, int(obs.reasoning_tokens))))
            if obs.account_id:
                self._proxy_account_tokens_total.labels(account_id=obs.account_id, kind="reasoning", api=api).inc(
                    float(max(0, int(obs.reasoning_tokens)))
                )

        cost = cost_from_log(obs, precision=None)
        if cost is not None and cost >= 0:
            self._proxy_cost_usd_total.labels(model=model).inc(float(cost))
            if obs.account_id:
                self._proxy_account_cost_usd_total.labels(account_id=obs.account_id, api=api).inc(float(cost))
        elif status == "success":
            reason = _unpriced_success_reason(obs)
            self._proxy_unpriced_success_total.labels(api=api, reason=reason).inc()
            if obs.account_id:
                self._proxy_account_unpriced_success_total.labels(
                    account_id=obs.account_id,
                    api=api,
                    reason=reason,
                ).inc()

    def observe_proxy_retry(self, *, api: str, error_code: str | None, account_id: str | None = None) -> None:
        api_value = api or "unknown"
        error_class = _error_class(error_code)
        self._proxy_retries_total.labels(api=api_value, error_class=error_class).inc()
        if account_id:
            self._proxy_account_retries_total.labels(
                account_id=account_id,
                api=api_value,
                error_class=error_class,
            ).inc()

    def set_request_log_buffer_size(self, size: int) -> None:
        self._request_log_buffer_size.set(float(max(0, int(size))))

    def inc_request_log_buffer_dropped(self) -> None:
        self._request_log_buffer_dropped_total.inc()

    def observe_lb_select(
        self,
        *,
        pool: str,
        sticky_backend: str,
        reallocate_sticky: bool,
        outcome: str,
    ) -> None:
        self._lb_select_total.labels(
            pool=pool or "unknown",
            sticky_backend=sticky_backend or "unknown",
            reallocate_sticky="true" if reallocate_sticky else "false",
            outcome=outcome or "unknown",
        ).inc()

    def observe_lb_tier_decision(
        self,
        *,
        pool: str,
        sticky_backend: str,
        reallocate_sticky: bool,
        outcome: str,
        selected_tier: str | None,
        tier_scores: Sequence[tuple[str, float]],
    ) -> None:
        labels = {
            "pool": pool or "unknown",
            "sticky_backend": sticky_backend or "unknown",
            "reallocate_sticky": "true" if reallocate_sticky else "false",
        }
        if outcome == "selected":
            self._lb_selected_tier_total.labels(
                **labels,
                tier=selected_tier or "unknown",
            ).inc()
        for tier, score in tier_scores:
            if not math.isfinite(score):
                continue
            self._lb_tier_score.labels(
                **labels,
                tier=tier or "unknown",
            ).observe(max(0.0, float(score)))

    def observe_lb_mark(self, *, event: str, account_id: str) -> None:
        self._lb_mark_total.labels(event=event or "unknown", account_id=account_id or "unknown").inc()

    def observe_lb_permanent_failure(self, *, code: str) -> None:
        self._lb_mark_permanent_failure_total.labels(code=code or "unknown").inc()

    def observe_lb_snapshot_refresh(self, *, updated_at_seconds: float) -> None:
        self._lb_snapshot_refresh_total.inc()
        if updated_at_seconds >= 0:
            self._lb_snapshot_updated_at_seconds.set(float(updated_at_seconds))

    def observe_usage_refresh_failure(self, *, status_code: int, phase: str) -> None:
        status = str(int(status_code)) if status_code >= 0 else "unknown"
        self._usage_refresh_failures_total.labels(status_code=status, phase=phase or "unknown").inc()

    def refresh_account_identity_gauges(
        self,
        observations: Sequence[AccountIdentityObservation],
        *,
        mode: Literal["email", "account_id"],
    ) -> None:
        current_ids = {item.account_id for item in observations if item.account_id}
        removed = self._known_account_identity_ids - current_ids
        if removed:
            for account_id in removed:
                prev = self._account_identity_state.pop(account_id, None)
                if prev is not None:
                    prev_display, prev_plan_type = prev
                    self._account_identity.remove(account_id, prev_display, prev_plan_type)
            self._known_account_identity_ids = set(current_ids)
        else:
            self._known_account_identity_ids = set(current_ids)

        for item in observations:
            display = item.email if mode == "email" else item.account_id
            canonical_plan_type = canonicalize_account_plan_type(item.plan_type)
            plan_type = canonical_plan_type.lower() if canonical_plan_type else "unknown"
            prev = self._account_identity_state.get(item.account_id)
            if prev is not None:
                prev_display, prev_plan_type = prev
                if prev_display != display or prev_plan_type != plan_type:
                    self._account_identity.remove(item.account_id, prev_display, prev_plan_type)
            self._account_identity.labels(account_id=item.account_id, display=display, plan_type=plan_type).set(1.0)
            self._account_identity_state[item.account_id] = (display, plan_type)

    def refresh_secondary_usage_gauges(
        self,
        *,
        status_values: Iterable[str],
        waste_inputs: Sequence[SecondaryWastePacingInput],
        now_epoch: int,
    ) -> None:
        current_account_ids = {item.account_id for item in waste_inputs if item.account_id}
        removed = self._known_account_ids - current_account_ids
        if removed:
            for account_id in removed:
                self._secondary_used_percent.remove(account_id)
                self._secondary_usage_state.pop(account_id, None)
                self._secondary_reset_at_seconds.remove(account_id)
                self._secondary_window_minutes.remove(account_id)
                self._secondary_remaining_credits.remove(account_id)
            self._known_account_ids = set(current_account_ids)
        else:
            self._known_account_ids = set(current_account_ids)

        counts = {
            "active": 0,
            "paused": 0,
            "limited": 0,
            "exceeded": 0,
            "deactivated": 0,
        }
        for raw in status_values:
            normalized = _normalize_account_status(raw)
            if normalized in counts:
                counts[normalized] += 1

        for key, count in counts.items():
            self._accounts_total.labels(status=key).set(float(count))

        for item in waste_inputs:
            account_id = item.account_id
            used = item.secondary_used_percent
            self._secondary_used_percent.labels(account_id=account_id).set(
                float(used) if used is not None else math.nan
            )
            reset_at = item.secondary_reset_at_epoch
            self._secondary_reset_at_seconds.labels(account_id=account_id).set(
                float(reset_at) if reset_at is not None else math.nan
            )
            window_minutes = item.secondary_window_minutes
            self._secondary_window_minutes.labels(account_id=account_id).set(
                float(window_minutes) if window_minutes is not None else math.nan
            )
            capacity = usage_core.capacity_for_plan(item.plan_type, "secondary")
            used_value = float(used) if used is not None else None
            used_credits = (
                usage_core.used_credits_from_percent(used_value, capacity)
                if used_value is not None and capacity is not None
                else None
            )
            remaining = (
                usage_core.remaining_credits_from_used(used_credits, capacity) if used_credits is not None else None
            )
            self._secondary_remaining_credits.labels(account_id=account_id).set(
                float(remaining) if remaining is not None else math.nan
            )
            self._observe_secondary_used_percent_progress(
                account_id=account_id,
                used_percent=used,
                reset_at_epoch=reset_at,
            )

    def refresh_secondary_quota_estimates_7d(
        self,
        observations: Sequence[SecondaryQuotaEstimateObservation],
    ) -> None:
        current_ids = {item.account_id for item in observations if item.account_id}
        removed = self._known_secondary_quota_ids - current_ids
        if removed:
            for account_id in removed:
                self._secondary_cost_usd_7d.remove(account_id)
                self._secondary_used_percent_delta_pp_7d.remove(account_id)
                self._secondary_implied_quota_usd_7d.remove(account_id)
            self._known_secondary_quota_ids = set(current_ids)
        else:
            self._known_secondary_quota_ids = set(current_ids)

        for item in observations:
            account_id = item.account_id
            cost_usd = float(item.cost_usd_7d)
            used_delta_pp = float(item.used_delta_pp_7d)
            self._secondary_cost_usd_7d.labels(account_id=account_id).set(cost_usd)
            self._secondary_used_percent_delta_pp_7d.labels(account_id=account_id).set(used_delta_pp)
            if used_delta_pp > 0:
                implied = cost_usd / (used_delta_pp / 100.0)
                self._secondary_implied_quota_usd_7d.labels(account_id=account_id).set(float(implied))
            else:
                self._secondary_implied_quota_usd_7d.labels(account_id=account_id).set(math.nan)

    def _observe_secondary_used_percent_progress(
        self,
        *,
        account_id: str,
        used_percent: float | None,
        reset_at_epoch: int | None,
    ) -> None:
        if used_percent is None:
            return
        used_value = float(used_percent)
        if math.isnan(used_value):
            return

        used_value = max(0.0, min(100.0, used_value))
        prev = self._secondary_usage_state.get(account_id)
        if prev is None:
            self._secondary_usage_state[account_id] = SecondaryUsagePercentState(
                used_percent=used_value,
                reset_at_epoch=reset_at_epoch,
            )
            return

        delta = used_value - prev.used_percent
        if delta < 0:
            # Treat decreases as reset only when reset_at advances materially (weekly boundary).
            if (
                reset_at_epoch is not None
                and prev.reset_at_epoch is not None
                and int(reset_at_epoch) > int(prev.reset_at_epoch) + 3600
            ):
                self._secondary_resets_total.labels(account_id=account_id).inc()
            # Otherwise treat it as noise and do not count as a reset.

        self._secondary_usage_state[account_id] = SecondaryUsagePercentState(
            used_percent=used_value,
            reset_at_epoch=reset_at_epoch,
        )
