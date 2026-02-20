from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

import pytest

from app.core.balancer.logic import AccountState
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.proxy.load_balancer import LoadBalancer, _Snapshot


@asynccontextmanager
async def _unused_repo_factory():  # pragma: no cover
    raise AssertionError("repo_factory should not be used by this test")
    yield  # noqa: B018


def _make_account(account_id: str, email: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=None,
        email=email,
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
        reset_at=None,
    )


@pytest.mark.asyncio
async def test_lb_logs_pinned_fallback_summary(caplog) -> None:
    lb = LoadBalancer(_unused_repo_factory)
    lb._snapshot_ttl_seconds = 60_000.0  # avoid refresh

    pinned = _make_account("abc_pinned", "pinned@example.com")
    full = _make_account("xyz_full", "full@example.com")

    now = time.time()
    pinned_state = AccountState(
        account_id=pinned.id,
        status=AccountStatus.ACTIVE,
        used_percent=0.0,
        reset_at=None,
        cooldown_until=now + 3600,
        secondary_used_percent=0.0,
        secondary_reset_at=None,
        secondary_capacity_credits=400.0,
        last_error_at=now,
        last_selected_at=None,
        error_count=1,
        deactivation_reason=None,
    )
    full_state = AccountState(
        account_id=full.id,
        status=AccountStatus.ACTIVE,
        used_percent=0.0,
        reset_at=None,
        cooldown_until=None,
        secondary_used_percent=0.0,
        secondary_reset_at=None,
        secondary_capacity_credits=400.0,
        last_error_at=None,
        last_selected_at=None,
        error_count=0,
        deactivation_reason=None,
    )

    snapshot = _Snapshot(
        accounts=[pinned, full],
        latest_primary={},
        latest_secondary={},
        states=[pinned_state, full_state],
        account_map={pinned.id: pinned, full.id: full},
        pinned_account_ids=frozenset({pinned.id}),
        updated_at=now,
    )

    lb._snapshot = snapshot
    lb._pinned_settings_checked_at = time.time()
    lb._pinned_settings_cached_ids = (pinned.id,)

    caplog.set_level(logging.INFO, logger="app.modules.proxy.load_balancer")
    selection = await lb.select_account()
    assert selection.account is not None
    assert selection.account.email == "full@example.com"

    messages = [record.getMessage() for record in caplog.records]
    assert any("lb_fallback pinned_failed" in msg for msg in messages)
    assert any("full_selected=full@example.com[xyz]" in msg for msg in messages)
