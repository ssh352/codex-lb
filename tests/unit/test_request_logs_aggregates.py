from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.utils.time import utcnow
from app.db.session import SessionLocal
from app.modules.request_logs.repository import RequestLogsRepository


@pytest.mark.asyncio
async def test_aggregate_usage_since_sums_tokens_and_cost_inputs(db_setup) -> None:
    now = utcnow()
    since = now - timedelta(days=1)

    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        # Cost-eligible, cached tokens included.
        await repo.add_log(
            account_id="acc",
            request_id="req_1",
            model="gpt-5.1",
            input_tokens=1000,
            output_tokens=500,
            cached_input_tokens=200,
            reasoning_tokens=None,
            latency_ms=1,
            status="success",
            error_code=None,
            requested_at=now - timedelta(hours=2),
        )

        # Cost-eligible, output derived from reasoning, cached clamped to input.
        await repo.add_log(
            account_id="acc",
            request_id="req_2",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=None,
            cached_input_tokens=999,
            reasoning_tokens=25,
            latency_ms=1,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=now - timedelta(hours=1),
        )

        # Not cost-eligible (missing input tokens) but still contributes to total token sums.
        await repo.add_log(
            account_id="acc",
            request_id="req_3",
            model="gpt-5.1",
            input_tokens=None,
            output_tokens=10,
            cached_input_tokens=5,
            reasoning_tokens=None,
            latency_ms=1,
            status="error",
            error_code="quota_exceeded",
            requested_at=now - timedelta(minutes=30),
        )

        aggregates = await repo.aggregate_usage_since(since)

    assert aggregates.total_requests == 3
    assert aggregates.error_requests == 2
    assert aggregates.top_error in {"quota_exceeded", "rate_limit_exceeded"}
    # tokens = (1000+500) + (100+25) + (0+10)
    assert aggregates.tokens_sum == 1635
    # cached: 200 + min(999,100) + 5 (no input clamp)
    assert aggregates.cached_input_tokens_sum == 305

    assert [entry.model for entry in aggregates.by_model] == ["gpt-5.1"]
    model = aggregates.by_model[0]
    assert model.input_tokens_sum == 1100
    assert model.output_tokens_sum == 525
    assert model.cached_input_tokens_sum == 300
