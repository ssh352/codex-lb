from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest

from app.core.auth import fallback_account_id, generate_unique_account_id
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import AccountsSessionLocal, SessionLocal
from app.modules.accounts.repository import AccountsRepository, AccountStatusUpdate
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str | None, email: str, plan_type: str = "plus") -> dict:
    payload = {
        "email": email,
        "https://api.openai.com/auth": {"chatgpt_plan_type": plan_type},
    }
    if account_id:
        payload["chatgpt_account_id"] = account_id
    tokens: dict[str, object] = {
        "idToken": _encode_jwt(payload),
        "accessToken": "access",
        "refreshToken": "refresh",
    }
    if account_id:
        tokens["accountId"] = account_id
    return {"tokens": tokens}


def _make_account(
    account_id: str,
    email: str,
    plan_type: str = "plus",
    *,
    status: AccountStatus = AccountStatus.ACTIVE,
    reset_at: int | None = None,
) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=status,
        deactivation_reason=None,
        reset_at=reset_at,
    )


def _iso_utc(epoch_seconds: int) -> str:
    return (
        datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )


@pytest.mark.asyncio
async def test_import_invalid_json_returns_400(async_client):
    files = {"auth_json": ("auth.json", "not-json", "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_auth_json"


@pytest.mark.asyncio
async def test_import_missing_tokens_returns_400(async_client):
    files = {"auth_json": ("auth.json", json.dumps({"foo": "bar"}), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_auth_json"


@pytest.mark.asyncio
async def test_import_falls_back_to_email_based_account_id(async_client):
    email = "fallback@example.com"
    auth_json = _make_auth_json(None, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    payload = response.json()
    assert payload["accountId"] == fallback_account_id(email)
    assert payload["email"] == email


@pytest.mark.asyncio
async def test_delete_account_removes_from_list(async_client):
    email = "delete@example.com"
    raw_account_id = "acc_delete"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    actual_account_id = generate_unique_account_id(raw_account_id, email)
    delete = await async_client.delete(f"/api/accounts/{actual_account_id}")
    assert delete.status_code == 200
    assert delete.json()["status"] == "deleted"

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = accounts.json()["accounts"]
    assert all(account["accountId"] != actual_account_id for account in data)


@pytest.mark.asyncio
async def test_accounts_list_includes_per_account_reset_times(async_client, db_setup):
    primary_a = 1735689600
    primary_b = 1735693200
    secondary_a = 1736294400
    secondary_b = 1736380800

    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(_make_account("acc_reset_a", "a@example.com"))
        await accounts_repo.upsert(_make_account("acc_reset_b", "b@example.com"))

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            "acc_reset_a",
            10.0,
            window="primary",
            reset_at=primary_a,
        )
        await usage_repo.add_entry(
            "acc_reset_b",
            20.0,
            window="primary",
            reset_at=primary_b,
        )
        await usage_repo.add_entry(
            "acc_reset_a",
            30.0,
            window="secondary",
            reset_at=secondary_a,
        )
        await usage_repo.add_entry(
            "acc_reset_b",
            40.0,
            window="secondary",
            reset_at=secondary_b,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    assert accounts["acc_reset_a"]["resetAtPrimary"] == _iso_utc(primary_a)
    assert accounts["acc_reset_b"]["resetAtPrimary"] == _iso_utc(primary_b)
    assert accounts["acc_reset_a"]["resetAtSecondary"] == _iso_utc(secondary_a)
    assert accounts["acc_reset_b"]["resetAtSecondary"] == _iso_utc(secondary_b)


@pytest.mark.asyncio
async def test_accounts_list_includes_status_reset_at(async_client, db_setup):
    now_epoch = int(utcnow().replace(tzinfo=timezone.utc).timestamp())
    blocked_until = now_epoch + 3600

    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(
            _make_account(
                "acc_blocked",
                "blocked@example.com",
                status=AccountStatus.RATE_LIMITED,
                reset_at=blocked_until,
            )
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    matched = next((account for account in payload["accounts"] if account["accountId"] == "acc_blocked"), None)
    assert matched is not None
    assert matched["status"] == "rate_limited"
    assert matched["statusResetAt"] == _iso_utc(blocked_until)


@pytest.mark.asyncio
async def test_accounts_repository_clears_reset_at_when_status_becomes_active(db_setup):
    blocked_until = 1736294400

    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(
            _make_account(
                "acc_hygiene_active",
                "hygiene_active@example.com",
                status=AccountStatus.RATE_LIMITED,
                reset_at=blocked_until,
            )
        )

    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        updated = await accounts_repo.update_status(
            "acc_hygiene_active",
            AccountStatus.ACTIVE,
            None,
            reset_at=blocked_until,
        )
        assert updated is True

    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        account = await accounts_repo.get_account("acc_hygiene_active")
        assert account is not None
        assert account.status == AccountStatus.ACTIVE
        assert account.reset_at is None


@pytest.mark.asyncio
async def test_accounts_repository_bulk_update_clears_reset_at_when_not_blocked(db_setup):
    blocked_until = 1736294400

    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(
            _make_account(
                "acc_hygiene_pause",
                "hygiene_pause@example.com",
                status=AccountStatus.RATE_LIMITED,
                reset_at=blocked_until,
            )
        )

    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        updated = await accounts_repo.bulk_update_status_fields(
            [
                AccountStatusUpdate(
                    account_id="acc_hygiene_pause",
                    status=AccountStatus.PAUSED,
                    deactivation_reason=None,
                    reset_at=blocked_until,
                )
            ]
        )
        assert updated == 1

    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        account = await accounts_repo.get_account("acc_hygiene_pause")
        assert account is not None
        assert account.status == AccountStatus.PAUSED
        assert account.reset_at is None


@pytest.mark.asyncio
async def test_accounts_list_clears_stale_blocked_status(async_client, db_setup):
    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(
            _make_account(
                "acc_stale_blocked",
                "stale_blocked@example.com",
                status=AccountStatus.RATE_LIMITED,
                reset_at=1,
            )
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    matched = next((account for account in payload["accounts"] if account["accountId"] == "acc_stale_blocked"), None)
    assert matched is not None
    assert matched["status"] == "active"
    assert matched.get("statusResetAt") is None


@pytest.mark.asyncio
async def test_accounts_list_clears_reset_at_for_active_account(async_client, db_setup):
    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(
            _make_account(
                "acc_active_with_reset",
                "active_reset@example.com",
                status=AccountStatus.ACTIVE,
                reset_at=1736294400,
            )
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    matched = next(
        (account for account in payload["accounts"] if account["accountId"] == "acc_active_with_reset"),
        None,
    )
    assert matched is not None
    assert matched["status"] == "active"
    assert matched.get("statusResetAt") is None

    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        account = await accounts_repo.get_account("acc_active_with_reset")
        assert account is not None
        assert account.reset_at is None


@pytest.mark.asyncio
async def test_accounts_list_does_not_clear_blocked_status_when_reset_in_future(async_client, db_setup):
    now_epoch = int(utcnow().replace(tzinfo=timezone.utc).timestamp())
    blocked_until = now_epoch + 3600

    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(
            _make_account(
                "acc_future_blocked",
                "future_blocked@example.com",
                status=AccountStatus.RATE_LIMITED,
                reset_at=blocked_until,
            )
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    matched = next((account for account in payload["accounts"] if account["accountId"] == "acc_future_blocked"), None)
    assert matched is not None
    assert matched["status"] == "rate_limited"
    assert matched["statusResetAt"] == _iso_utc(blocked_until)


@pytest.mark.asyncio
async def test_accounts_list_does_not_clear_blocked_status_when_reset_unknown(async_client, db_setup):
    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(
            _make_account(
                "acc_unknown_blocked",
                "unknown_blocked@example.com",
                status=AccountStatus.RATE_LIMITED,
                reset_at=None,
            )
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    matched = next((account for account in payload["accounts"] if account["accountId"] == "acc_unknown_blocked"), None)
    assert matched is not None
    assert matched["status"] == "rate_limited"
    assert matched.get("statusResetAt") is None


@pytest.mark.asyncio
async def test_accounts_list_clears_stale_rate_limited_status_from_usage_reset(async_client, db_setup):
    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(
            _make_account(
                "acc_stale_rate_limited_usage",
                "stale_rate_limited_usage@example.com",
                status=AccountStatus.RATE_LIMITED,
                reset_at=None,
            )
        )

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            "acc_stale_rate_limited_usage",
            100.0,
            window="primary",
            reset_at=1,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    matched = next(
        (account for account in payload["accounts"] if account["accountId"] == "acc_stale_rate_limited_usage"),
        None,
    )
    assert matched is not None
    assert matched["status"] == "active"
    assert matched.get("statusResetAt") is None


@pytest.mark.asyncio
async def test_accounts_list_clears_stale_quota_exceeded_status_from_usage_reset(async_client, db_setup):
    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(
            _make_account(
                "acc_stale_quota_exceeded_usage",
                "stale_quota_exceeded_usage@example.com",
                status=AccountStatus.QUOTA_EXCEEDED,
                reset_at=None,
            )
        )

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            "acc_stale_quota_exceeded_usage",
            100.0,
            window="secondary",
            reset_at=1,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    matched = next(
        (account for account in payload["accounts"] if account["accountId"] == "acc_stale_quota_exceeded_usage"),
        None,
    )
    assert matched is not None
    assert matched["status"] == "active"
    assert matched.get("statusResetAt") is None


@pytest.mark.asyncio
async def test_accounts_list_status_reset_at_falls_back_to_usage_resets(async_client, db_setup):
    now_epoch = int(utcnow().replace(tzinfo=timezone.utc).timestamp())
    primary_reset_at = now_epoch + 3600
    secondary_reset_at = now_epoch + 7200

    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(
            _make_account(
                "acc_fallback_primary",
                "fallback_primary@example.com",
                status=AccountStatus.RATE_LIMITED,
                reset_at=None,
            )
        )
        await accounts_repo.upsert(
            _make_account(
                "acc_fallback_secondary",
                "fallback_secondary@example.com",
                status=AccountStatus.QUOTA_EXCEEDED,
                reset_at=None,
            )
        )

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            "acc_fallback_primary",
            50.0,
            window="primary",
            reset_at=primary_reset_at,
        )
        await usage_repo.add_entry(
            "acc_fallback_secondary",
            100.0,
            window="secondary",
            reset_at=secondary_reset_at,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    assert accounts["acc_fallback_primary"]["status"] == "rate_limited"
    assert accounts["acc_fallback_primary"]["statusResetAt"] == _iso_utc(primary_reset_at)
    assert accounts["acc_fallback_secondary"]["status"] == "quota_exceeded"
    assert accounts["acc_fallback_secondary"]["statusResetAt"] == _iso_utc(secondary_reset_at)


@pytest.mark.asyncio
async def test_accounts_list_status_reset_at_uses_latest_reset(async_client, db_setup):
    now_epoch = int(utcnow().replace(tzinfo=timezone.utc).timestamp())
    stale_reset_at = now_epoch + 60
    latest_reset_at = now_epoch + 120

    async with AccountsSessionLocal() as accounts_session:
        accounts_repo = AccountsRepository(accounts_session)
        await accounts_repo.upsert(
            _make_account(
                "acc_latest_reset",
                "latest_reset@example.com",
                status=AccountStatus.RATE_LIMITED,
                reset_at=stale_reset_at,
            )
        )

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            "acc_latest_reset",
            50.0,
            window="primary",
            reset_at=latest_reset_at,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    assert accounts["acc_latest_reset"]["status"] == "rate_limited"
    assert accounts["acc_latest_reset"]["statusResetAt"] == _iso_utc(latest_reset_at)
