from __future__ import annotations

import time

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.proxy.load_balancer import RuntimeState, _state_from_account


def _make_account(*, status: AccountStatus, reset_at: int | None) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id="acc_test",
        chatgpt_account_id=None,
        email="test@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=status,
        deactivation_reason=None,
        reset_at=reset_at,
    )


def test_state_from_account_clears_expired_runtime_reset_and_uses_db_reset(monkeypatch) -> None:
    fixed_now = 1_000.0
    monkeypatch.setattr(time, "time", lambda: fixed_now)

    account = _make_account(status=AccountStatus.RATE_LIMITED, reset_at=2_000)
    runtime = RuntimeState(reset_at=900.0)

    state = _state_from_account(account=account, primary_entry=None, secondary_entry=None, runtime=runtime)
    assert runtime.reset_at is None
    assert state.status == AccountStatus.RATE_LIMITED
    assert state.reset_at == pytest.approx(2_000.0)


def test_state_from_account_uses_max_of_runtime_and_db_reset(monkeypatch) -> None:
    fixed_now = 1_000.0
    monkeypatch.setattr(time, "time", lambda: fixed_now)

    account = _make_account(status=AccountStatus.RATE_LIMITED, reset_at=3_000)
    runtime = RuntimeState(reset_at=2_000.0)

    state = _state_from_account(account=account, primary_entry=None, secondary_entry=None, runtime=runtime)
    assert state.reset_at == pytest.approx(3_000.0)
