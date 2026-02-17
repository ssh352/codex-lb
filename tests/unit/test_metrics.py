from __future__ import annotations

from prometheus_client import CollectorRegistry
from prometheus_client.parser import text_string_to_metric_families

from app.core.metrics.metrics import (
    AccountIdentityObservation,
    Metrics,
    ProxyRequestObservation,
    SecondaryQuotaEstimateObservation,
)
from app.core.usage.waste_pacing import SecondaryWastePacingInput


def _sample_value(text: str, metric_name: str, labels: dict[str, str] | None = None) -> float | None:
    target_labels = labels or {}
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name != metric_name:
                continue
            if all(sample.labels.get(k) == v for k, v in target_labels.items()):
                return float(sample.value)
    return None


def test_metrics_observes_proxy_request() -> None:
    registry = CollectorRegistry(auto_describe=True)
    metrics = Metrics(registry=registry)
    metrics.observe_proxy_request(
        ProxyRequestObservation(
            account_id="acc_test",
            api="responses",
            status="success",
            model="gpt-5.1",
            latency_ms=123,
            input_tokens=100,
            output_tokens=50,
            cached_input_tokens=10,
            reasoning_tokens=None,
            error_code=None,
        )
    )

    rendered = metrics.render().decode("utf-8")
    assert (
        _sample_value(
            rendered,
            "codex_lb_proxy_requests_total",
            {"status": "success", "model": "gpt-5.1", "api": "responses"},
        )
        == 1.0
    )
    assert (
        _sample_value(
            rendered,
            "codex_lb_proxy_account_requests_total",
            {"account_id": "acc_test", "status": "success", "api": "responses"},
        )
        == 1.0
    )
    assert _sample_value(rendered, "codex_lb_proxy_tokens_total", {"kind": "input", "model": "gpt-5.1"}) == 100.0
    assert _sample_value(rendered, "codex_lb_proxy_tokens_total", {"kind": "output", "model": "gpt-5.1"}) == 50.0
    assert _sample_value(rendered, "codex_lb_proxy_tokens_total", {"kind": "cached_input", "model": "gpt-5.1"}) == 10.0
    assert (
        _sample_value(
            rendered,
            "codex_lb_proxy_account_tokens_total",
            {"account_id": "acc_test", "kind": "input", "api": "responses"},
        )
        == 100.0
    )

    metrics.observe_proxy_request(
        ProxyRequestObservation(
            account_id="acc_test",
            api="responses",
            status="success",
            model="unknown-model",
            latency_ms=5,
            input_tokens=10,
            output_tokens=5,
            cached_input_tokens=0,
            reasoning_tokens=None,
            error_code=None,
        )
    )
    rendered = metrics.render().decode("utf-8")
    assert (
        _sample_value(
            rendered,
            "codex_lb_proxy_unpriced_success_total",
            {"api": "responses", "reason": "unknown_pricing"},
        )
        == 1.0
    )
    assert (
        _sample_value(
            rendered,
            "codex_lb_proxy_account_unpriced_success_total",
            {"account_id": "acc_test", "api": "responses", "reason": "unknown_pricing"},
        )
        == 1.0
    )


def test_metrics_observes_proxy_retry() -> None:
    registry = CollectorRegistry(auto_describe=True)
    metrics = Metrics(registry=registry)
    metrics.observe_proxy_retry(
        api="responses_compact",
        error_code="server_error",
        account_id="acc_test",
    )
    rendered = metrics.render().decode("utf-8")
    assert (
        _sample_value(
            rendered,
            "codex_lb_proxy_retries_total",
            {"api": "responses_compact", "error_class": "upstream"},
        )
        == 1.0
    )
    assert (
        _sample_value(
            rendered,
            "codex_lb_proxy_account_retries_total",
            {"account_id": "acc_test", "api": "responses_compact", "error_class": "upstream"},
        )
        == 1.0
    )


def test_metrics_refreshes_account_identity() -> None:
    registry = CollectorRegistry(auto_describe=True)
    metrics = Metrics(registry=registry)
    metrics.refresh_account_identity_gauges(
        [AccountIdentityObservation(account_id="acc_a", email="a@example.com", plan_type="plus")],
        mode="email",
    )
    rendered = metrics.render().decode("utf-8")
    assert (
        _sample_value(
            rendered,
            "codex_lb_account_identity",
            {"account_id": "acc_a", "display": "a@example.com", "plan_type": "plus"},
        )
        == 1.0
    )


def test_metrics_refreshes_secondary_usage_gauges() -> None:
    registry = CollectorRegistry(auto_describe=True)
    metrics = Metrics(registry=registry)
    now_epoch = 1_700_000_000

    waste_inputs = [
        SecondaryWastePacingInput(
            account_id="acc_a",
            plan_type="plus",
            secondary_used_percent=50.0,
            secondary_reset_at_epoch=now_epoch + 3_600,
            secondary_window_minutes=10080,
        ),
        SecondaryWastePacingInput(
            account_id="acc_b",
            plan_type="plus",
            secondary_used_percent=0.0,
            secondary_reset_at_epoch=now_epoch + 3_600,
            secondary_window_minutes=10080,
        ),
    ]

    metrics.refresh_secondary_usage_gauges(
        status_values=["active", "active"],
        waste_inputs=waste_inputs,
        now_epoch=now_epoch,
    )

    rendered = metrics.render().decode("utf-8")
    assert _sample_value(rendered, "codex_lb_secondary_used_percent", {"account_id": "acc_a"}) == 50.0
    assert _sample_value(rendered, "codex_lb_secondary_reset_at_seconds", {"account_id": "acc_a"}) == float(
        now_epoch + 3_600
    )
    assert _sample_value(rendered, "codex_lb_secondary_window_minutes", {"account_id": "acc_a"}) == 10080.0
    assert _sample_value(rendered, "codex_lb_secondary_remaining_credits", {"account_id": "acc_a"}) == 200.0

    assert _sample_value(rendered, "codex_lb_secondary_used_percent", {"account_id": "acc_b"}) == 0.0
    assert _sample_value(rendered, "codex_lb_secondary_remaining_credits", {"account_id": "acc_b"}) == 400.0
    assert _sample_value(rendered, "codex_lb_accounts_total", {"status": "active"}) == 2.0
    assert "codex_lb_secondary_projected_waste_credits" not in rendered
    assert "codex_lb_secondary_delta_needed_cph" not in rendered
    assert "codex_lb_secondary_pacing_known" not in rendered
    assert "codex_lb_secondary_projected_waste_credits_total" not in rendered
    assert "codex_lb_secondary_delta_needed_cph_total" not in rendered
    assert "codex_lb_secondary_accounts_evaluated" not in rendered
    assert "codex_lb_secondary_accounts_at_risk" not in rendered
    assert "codex_lb_secondary_used_percent_increase_total" not in rendered

    metrics.refresh_secondary_usage_gauges(
        status_values=["active"],
        waste_inputs=[
            SecondaryWastePacingInput(
                account_id="acc_reset",
                plan_type="plus",
                secondary_used_percent=90.0,
                secondary_reset_at_epoch=now_epoch + 10_000,
                secondary_window_minutes=10080,
            ),
        ],
        now_epoch=now_epoch,
    )
    metrics.refresh_secondary_usage_gauges(
        status_values=["active"],
        waste_inputs=[
            SecondaryWastePacingInput(
                account_id="acc_reset",
                plan_type="plus",
                secondary_used_percent=10.0,
                secondary_reset_at_epoch=now_epoch + 100_000,
                secondary_window_minutes=10080,
            ),
        ],
        now_epoch=now_epoch,
    )
    rendered = metrics.render().decode("utf-8")
    assert (
        _sample_value(
            rendered,
            "codex_lb_secondary_resets_total",
            {"account_id": "acc_reset"},
        )
        == 1.0
    )
    assert "codex_lb_secondary_used_percent_increase_total" not in rendered


def test_metrics_refreshes_secondary_quota_estimates_7d() -> None:
    registry = CollectorRegistry(auto_describe=True)
    metrics = Metrics(registry=registry)
    metrics.refresh_secondary_quota_estimates_7d(
        [
            SecondaryQuotaEstimateObservation(
                account_id="acc_a",
                cost_usd_7d=57.0,
                used_delta_pp_7d=10.0,
            )
        ]
    )
    rendered = metrics.render().decode("utf-8")
    assert _sample_value(rendered, "codex_lb_proxy_account_cost_usd_7d", {"account_id": "acc_a"}) == 57.0
    assert _sample_value(rendered, "codex_lb_secondary_used_percent_delta_pp_7d", {"account_id": "acc_a"}) == 10.0
    assert _sample_value(rendered, "codex_lb_secondary_implied_quota_usd_7d", {"account_id": "acc_a"}) == 570.0


def test_metrics_observes_load_balancer_events() -> None:
    registry = CollectorRegistry(auto_describe=True)
    metrics = Metrics(registry=registry)

    metrics.observe_lb_select(
        pool="full",
        sticky_backend="memory",
        reallocate_sticky=False,
        outcome="selected",
    )
    metrics.observe_lb_mark(event="rate_limit", account_id="acc_test")
    metrics.observe_lb_permanent_failure(code="refresh_token_expired")
    metrics.observe_lb_snapshot_refresh(updated_at_seconds=1_700_000_000.0)

    rendered = metrics.render().decode("utf-8")
    assert (
        _sample_value(
            rendered,
            "codex_lb_lb_select_total",
            {
                "pool": "full",
                "sticky_backend": "memory",
                "reallocate_sticky": "false",
                "outcome": "selected",
            },
        )
        == 1.0
    )
    assert (
        _sample_value(
            rendered,
            "codex_lb_lb_mark_total",
            {"event": "rate_limit", "account_id": "acc_test"},
        )
        == 1.0
    )
    assert (
        _sample_value(
            rendered,
            "codex_lb_lb_mark_permanent_failure_total",
            {"code": "refresh_token_expired"},
        )
        == 1.0
    )
    assert _sample_value(rendered, "codex_lb_lb_snapshot_refresh_total") == 1.0
    assert _sample_value(rendered, "codex_lb_lb_snapshot_updated_at_seconds") == 1_700_000_000.0
