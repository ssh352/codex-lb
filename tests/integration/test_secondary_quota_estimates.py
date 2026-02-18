from __future__ import annotations

from datetime import timedelta

import pytest
from prometheus_client.parser import text_string_to_metric_families

from app.core.crypto import TokenEncryptor
from app.core.utils.time import to_epoch_seconds_assuming_utc, utcnow
from app.db.models import Account, AccountStatus, RequestLog, UsageHistory
from app.db.session import AccountsSessionLocal, SessionLocal
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.integration


def _make_account(account_id: str, email: str, plan_type: str = "plus") -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _sample_value(text: str, metric_name: str, labels: dict[str, str] | None = None) -> float | None:
    target_labels = labels or {}
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name != metric_name:
                continue
            if all(sample.labels.get(key) == value for key, value in target_labels.items()):
                return float(sample.value)
    return None


@pytest.mark.asyncio
async def test_secondary_quota_estimates_clip_to_most_recent_reset(async_client, db_setup) -> None:
    # Force the /metrics endpoint to refresh quota gauges even if other integration tests ran recently.
    import app.modules.metrics.api as metrics_api

    metrics_api._last_secondary_quota_refresh_monotonic = 0.0

    now = utcnow().replace(microsecond=0)
    account_id = "acc_quota_reset"

    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(_make_account(account_id, "quota-reset@example.com"))

    # Model: gpt-5
    # Cost(log_a) = (1_000_000/1e6)*1.25 + (100_000/1e6)*10 = 2.25
    # Cost(log_b) = (500_000/1e6)*1.25 + (50_000/1e6)*10 = 1.125
    expected_cost = 3.375
    expected_used_pp = 21.0  # Latest observed used% for the current cycle.
    expected_implied_quota = expected_cost / (expected_used_pp / 100.0)

    new_reset_at = to_epoch_seconds_assuming_utc(now + timedelta(days=2))
    reset_start = (now - timedelta(days=5)).replace(microsecond=0)

    async with SessionLocal() as session:
        session.add_all(
            [
                # First sample after the reset boundary (reset_at jump).
                UsageHistory(
                    account_id=account_id,
                    recorded_at=(reset_start + timedelta(minutes=10)),
                    window="secondary",
                    used_percent=0.0,
                    reset_at=new_reset_at,
                    window_minutes=10080,
                    input_tokens=None,
                    output_tokens=None,
                    credits_has=None,
                    credits_unlimited=None,
                    credits_balance=None,
                ),
                UsageHistory(
                    account_id=account_id,
                    recorded_at=(now - timedelta(hours=12)),
                    window="secondary",
                    used_percent=21.0,
                    reset_at=new_reset_at,
                    window_minutes=10080,
                    input_tokens=None,
                    output_tokens=None,
                    credits_has=None,
                    credits_unlimited=None,
                    credits_balance=None,
                ),
                # Pre-reset spend (should be excluded).
                RequestLog(
                    account_id=account_id,
                    request_id="pre-reset",
                    requested_at=(now - timedelta(days=6, hours=1)),
                    model="gpt-5",
                    input_tokens=1_000_000,
                    output_tokens=100_000,
                    cached_input_tokens=0,
                    reasoning_tokens=None,
                    reasoning_effort=None,
                    latency_ms=1,
                    status="success",
                    error_code=None,
                    error_message=None,
                ),
                # Post-reset: included.
                RequestLog(
                    account_id=account_id,
                    request_id="post-reset-a",
                    requested_at=(now - timedelta(days=4) + timedelta(hours=1)),
                    model="gpt-5",
                    input_tokens=1_000_000,
                    output_tokens=100_000,
                    cached_input_tokens=0,
                    reasoning_tokens=None,
                    reasoning_effort=None,
                    latency_ms=1,
                    status="success",
                    error_code=None,
                    error_message=None,
                ),
                # Post-reset: included.
                RequestLog(
                    account_id=account_id,
                    request_id="post-reset-b",
                    requested_at=now - timedelta(hours=1),
                    model="gpt-5",
                    input_tokens=500_000,
                    output_tokens=50_000,
                    cached_input_tokens=0,
                    reasoning_tokens=None,
                    reasoning_effort=None,
                    latency_ms=1,
                    status="success",
                    error_code=None,
                    error_message=None,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get("/metrics")
    assert response.status_code == 200
    body = response.text

    cost = _sample_value(body, "codex_lb_proxy_account_cost_usd_7d", {"account_id": account_id})
    assert cost is not None
    assert cost == pytest.approx(expected_cost, rel=1e-9)

    delta_pp = _sample_value(body, "codex_lb_secondary_used_percent_delta_pp_7d", {"account_id": account_id})
    assert delta_pp is not None
    assert delta_pp == pytest.approx(expected_used_pp, rel=1e-9)

    implied = _sample_value(body, "codex_lb_secondary_implied_quota_usd_7d", {"account_id": account_id})
    assert implied is not None
    assert implied == pytest.approx(expected_implied_quota, rel=1e-9)


@pytest.mark.asyncio
async def test_secondary_quota_estimates_exclude_missing_cycle_start_logs(async_client, db_setup) -> None:
    import app.modules.metrics.api as metrics_api

    metrics_api._last_secondary_quota_refresh_monotonic = 0.0

    now = utcnow().replace(microsecond=0)
    account_id = "acc_quota_external"

    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(_make_account(account_id, "quota-external@example.com"))

    new_reset_at = to_epoch_seconds_assuming_utc(now + timedelta(days=2))
    reset_start = (now - timedelta(days=5)).replace(microsecond=0)
    first_usage_at = reset_start + timedelta(days=3)

    async with SessionLocal() as session:
        session.add_all(
            [
                # First observed cycle sample is already materially >0 (missing early-cycle data).
                UsageHistory(
                    account_id=account_id,
                    recorded_at=first_usage_at,
                    window="secondary",
                    used_percent=18.0,
                    reset_at=new_reset_at,
                    window_minutes=10080,
                    input_tokens=None,
                    output_tokens=None,
                    credits_has=None,
                    credits_unlimited=None,
                    credits_balance=None,
                ),
                UsageHistory(
                    account_id=account_id,
                    recorded_at=(now - timedelta(hours=12)),
                    window="secondary",
                    used_percent=100.0,
                    reset_at=new_reset_at,
                    window_minutes=10080,
                    input_tokens=None,
                    output_tokens=None,
                    credits_has=None,
                    credits_unlimited=None,
                    credits_balance=None,
                ),
                # Proxy activity starts only after the first usage sample.
                RequestLog(
                    account_id=account_id,
                    request_id="post-first-usage",
                    requested_at=first_usage_at + timedelta(hours=1),
                    model="gpt-5",
                    input_tokens=1_000_000,
                    output_tokens=100_000,
                    cached_input_tokens=0,
                    reasoning_tokens=None,
                    reasoning_effort=None,
                    latency_ms=1,
                    status="success",
                    error_code=None,
                    error_message=None,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get("/metrics")
    assert response.status_code == 200
    body = response.text

    assert _sample_value(body, "codex_lb_proxy_account_cost_usd_7d", {"account_id": account_id}) is None
    assert _sample_value(body, "codex_lb_secondary_used_percent_delta_pp_7d", {"account_id": account_id}) is None
    assert _sample_value(body, "codex_lb_secondary_implied_quota_usd_7d", {"account_id": account_id}) is None


@pytest.mark.asyncio
async def test_secondary_quota_estimates_exclude_unexplained_mid_cycle_jump(async_client, db_setup) -> None:
    # Force the /metrics endpoint to refresh quota gauges even if other integration tests ran recently.
    import app.modules.metrics.api as metrics_api

    metrics_api._last_secondary_quota_refresh_monotonic = 0.0

    now = utcnow().replace(microsecond=0)
    account_id = "acc_quota_jump"

    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(_make_account(account_id, "quota-jump@example.com"))

    new_reset_at = to_epoch_seconds_assuming_utc(now + timedelta(days=2))
    reset_start = (now - timedelta(days=5)).replace(microsecond=0)

    async with SessionLocal() as session:
        session.add_all(
            [
                # Cycle start samples are present quickly (so this does NOT trigger the cycle-start exclusion).
                UsageHistory(
                    account_id=account_id,
                    recorded_at=(reset_start + timedelta(minutes=10)),
                    window="secondary",
                    used_percent=0.0,
                    reset_at=new_reset_at,
                    window_minutes=10080,
                    input_tokens=None,
                    output_tokens=None,
                    credits_has=None,
                    credits_unlimited=None,
                    credits_balance=None,
                ),
                UsageHistory(
                    account_id=account_id,
                    recorded_at=(reset_start + timedelta(minutes=20)),
                    window="secondary",
                    used_percent=10.0,
                    reset_at=new_reset_at,
                    window_minutes=10080,
                    input_tokens=None,
                    output_tokens=None,
                    credits_has=None,
                    credits_unlimited=None,
                    credits_balance=None,
                ),
                # Large unexplained jump after a long gap with no proxy logs.
                UsageHistory(
                    account_id=account_id,
                    recorded_at=(reset_start + timedelta(days=2)),
                    window="secondary",
                    used_percent=70.0,
                    reset_at=new_reset_at,
                    window_minutes=10080,
                    input_tokens=None,
                    output_tokens=None,
                    credits_has=None,
                    credits_unlimited=None,
                    credits_balance=None,
                ),
                UsageHistory(
                    account_id=account_id,
                    recorded_at=(now - timedelta(hours=1)),
                    window="secondary",
                    used_percent=100.0,
                    reset_at=new_reset_at,
                    window_minutes=10080,
                    input_tokens=None,
                    output_tokens=None,
                    credits_has=None,
                    credits_unlimited=None,
                    credits_balance=None,
                ),
                # Proxy activity exists later in the cycle, but not during the long-gap interval above.
                RequestLog(
                    account_id=account_id,
                    request_id="post-jump",
                    requested_at=reset_start + timedelta(days=2, hours=1),
                    model="gpt-5",
                    input_tokens=1_000_000,
                    output_tokens=100_000,
                    cached_input_tokens=0,
                    reasoning_tokens=None,
                    reasoning_effort=None,
                    latency_ms=1,
                    status="success",
                    error_code=None,
                    error_message=None,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get("/metrics")
    assert response.status_code == 200
    body = response.text

    assert _sample_value(body, "codex_lb_proxy_account_cost_usd_7d", {"account_id": account_id}) is None
    assert _sample_value(body, "codex_lb_secondary_used_percent_delta_pp_7d", {"account_id": account_id}) is None
    assert _sample_value(body, "codex_lb_secondary_implied_quota_usd_7d", {"account_id": account_id}) is None
