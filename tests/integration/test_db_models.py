from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import AccountsSessionLocal, SessionLocal, get_session

pytestmark = pytest.mark.integration


def _make_account(account_id: str, email: str, status: AccountStatus) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=status,
        deactivation_reason=None,
    )


@pytest.mark.asyncio
async def test_duplicate_emails_rejected(db_setup):
    async with AccountsSessionLocal() as session:
        session.add(_make_account("acc1", "dup@example.com", AccountStatus.ACTIVE))
        await session.commit()

        session.add(_make_account("acc2", "dup@example.com", AccountStatus.ACTIVE))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_status_enum_rejects_invalid_value(db_setup):
    async with AccountsSessionLocal() as session:
        account = _make_account("acc3", "enum@example.com", AccountStatus.ACTIVE)
        session.add(account)
        await session.commit()

        bad = _make_account("acc4", "enum2@example.com", AccountStatus.ACTIVE)
        bad.status = "invalid"  # type: ignore[assignment]
        session.add(bad)
        with pytest.raises((LookupError, StatementError)):
            await session.commit()


def _make_log() -> RequestLog:
    return RequestLog(
        account_id="acc5",
        request_id="req_rollback",
        model="gpt-5.2",
        status="success",
        error_code=None,
        error_message=None,
        requested_at=utcnow(),
        input_tokens=None,
        output_tokens=None,
        cached_input_tokens=None,
        reasoning_tokens=None,
        reasoning_effort=None,
        latency_ms=None,
    )


@pytest.mark.asyncio
async def test_get_session_rolls_back_on_error(db_setup, monkeypatch):
    called = {"rollback": False}
    original = AsyncSession.rollback

    async def wrapped(self):
        called["rollback"] = True
        await original(self)

    monkeypatch.setattr(AsyncSession, "rollback", wrapped)

    with pytest.raises(RuntimeError):
        async for session in get_session():
            session.add(_make_log())
            raise RuntimeError("boom")

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).where(RequestLog.request_id == "req_rollback"))
        assert result.scalar_one_or_none() is None

    assert called["rollback"] is True
