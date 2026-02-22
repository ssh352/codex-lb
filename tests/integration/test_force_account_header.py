from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import AccountsSessionLocal, SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.settings.repository import SettingsRepository

pytestmark = pytest.mark.integration


def _make_account(account_id: str, email: str, chatgpt_account_id: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=chatgpt_account_id,
        email=email,
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


@pytest.mark.asyncio
async def test_force_account_header_routes_request_to_forced_account(async_client, db_setup):
    account_pinned = _make_account("acc_pinned", "pinned@example.com", "chatgpt_pinned")
    account_forced = _make_account("acc_forced", "forced@example.com", "chatgpt_forced")

    async with AccountsSessionLocal() as accounts_session:
        repo = AccountsRepository(accounts_session)
        await repo.upsert(account_pinned)
        await repo.upsert(account_forced)

    async with SessionLocal() as session:
        settings_repo = SettingsRepository(session)
        await settings_repo.update(pinned_account_ids=[account_pinned.id])

    request_id = "req_force_account_1"
    payload = {"model": "gpt-5.1", "input": "hi", "stream": True}
    headers = {
        "x-request-id": request_id,
        "x-codex-lb-force-account-id": account_forced.id,
    }

    async with async_client.stream("POST", "/v1/responses", json=payload, headers=headers) as resp:
        assert resp.status_code == 200
        # Consume the stream to ensure request logging runs.
        lines = [line async for line in resp.aiter_lines() if line]
        assert lines

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).where(RequestLog.request_id == request_id))
        logs = list(result.scalars().all())

    assert logs
    assert {log.account_id for log in logs} == {account_forced.id}
