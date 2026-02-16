from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.usage import (
    capacity_for_plan,
    normalize_usage_window,
    summarize_usage_window,
    used_credits_from_percent,
)
from app.core.usage.types import UsageWindowRow, UsageWindowSummary
from app.db.models import Account, AccountStatus

pytestmark = pytest.mark.unit


def test_used_credits_from_percent():
    assert used_credits_from_percent(25.0, 200.0) == 50.0
    assert used_credits_from_percent(None, 200.0) is None


def test_normalize_usage_window_defaults():
    summary = UsageWindowSummary(
        used_percent=None,
        capacity_credits=0.0,
        used_credits=0.0,
        reset_at=None,
        window_minutes=None,
    )
    window = normalize_usage_window(summary)
    assert window.used_percent == 0.0
    assert window.capacity_credits == 0.0
    assert window.used_credits == 0.0


def test_capacity_for_plan():
    assert capacity_for_plan("plus", "5h") is not None
    assert capacity_for_plan("plus", "7d") is not None
    assert capacity_for_plan("free", "7d") == 100.0
    assert capacity_for_plan("unknown", "5h") is None


def test_summarize_usage_window_clamps_primary_window_minutes_to_hours():
    account = Account(
        id="acc_usage_primary",
        email="primary@example.com",
        plan_type="plus",
        access_token_encrypted=b"x",
        refresh_token_encrypted=b"x",
        id_token_encrypted=b"x",
        last_refresh=datetime.now(timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    row = UsageWindowRow(
        account_id=account.id,
        used_percent=10.0,
        reset_at=123,
        window_minutes=10080,  # bogus (7d) for primary
    )
    summary = summarize_usage_window([row], {account.id: account}, "primary")
    assert summary.window_minutes == 300


def test_summarize_usage_window_clamps_secondary_window_minutes_to_days():
    account = Account(
        id="acc_usage_secondary",
        email="secondary@example.com",
        plan_type="plus",
        access_token_encrypted=b"x",
        refresh_token_encrypted=b"x",
        id_token_encrypted=b"x",
        last_refresh=datetime.now(timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    row = UsageWindowRow(
        account_id=account.id,
        used_percent=10.0,
        reset_at=123,
        window_minutes=300,  # bogus (5h) for secondary
    )
    summary = summarize_usage_window([row], {account.id: account}, "secondary")
    assert summary.window_minutes == 10080


def test_summarize_usage_window_secondary_reset_at_is_earliest_across_accounts():
    account_a = Account(
        id="acc_secondary_a",
        email="a@example.com",
        plan_type="plus",
        access_token_encrypted=b"x",
        refresh_token_encrypted=b"x",
        id_token_encrypted=b"x",
        last_refresh=datetime.now(timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account_b = Account(
        id="acc_secondary_b",
        email="b@example.com",
        plan_type="plus",
        access_token_encrypted=b"x",
        refresh_token_encrypted=b"x",
        id_token_encrypted=b"x",
        last_refresh=datetime.now(timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )

    rows = [
        UsageWindowRow(
            account_id=account_a.id,
            used_percent=10.0,
            reset_at=500,
            window_minutes=10080,
        ),
        UsageWindowRow(
            account_id=account_b.id,
            used_percent=10.0,
            reset_at=1000,
            window_minutes=10080,
        ),
    ]

    summary = summarize_usage_window(rows, {account_a.id: account_a, account_b.id: account_b}, "secondary")
    assert summary.reset_at == 500
