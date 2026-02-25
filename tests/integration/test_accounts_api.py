from __future__ import annotations

import base64
import json

import pytest

from app.core.auth import generate_unique_account_id
from app.core.clients.proxy import ProxyResponseError
from app.core.openai.models import OpenAIResponsePayload
from app.db.models import Account, AccountStatus
from app.db.session import AccountsSessionLocal

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


@pytest.mark.asyncio
async def test_import_and_list_accounts(async_client):
    email = "tester@example.com"
    raw_account_id = "acc_explicit"
    payload = {
        "email": email,
        "chatgpt_account_id": "acc_payload",
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access",
            "refreshToken": "refresh",
            "accountId": raw_account_id,
        },
    }

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    data = response.json()
    assert data["accountId"] == expected_account_id
    assert data["email"] == email
    assert data["planType"] == "plus"

    list_response = await async_client.get("/api/accounts")
    assert list_response.status_code == 200
    accounts = list_response.json()["accounts"]
    assert any(account["accountId"] == expected_account_id for account in accounts)


@pytest.mark.asyncio
async def test_reactivate_missing_account_returns_404(async_client):
    response = await async_client.post("/api/accounts/missing/reactivate")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "account_not_found"


@pytest.mark.asyncio
async def test_reactivate_probes_then_sets_active(async_client, monkeypatch):
    from app.modules.proxy import service as proxy_service_mod

    email = "resume@example.com"
    raw_account_id = "acc_resume"
    payload = {
        "email": email,
        "chatgpt_account_id": raw_account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access",
            "refreshToken": "refresh",
            "accountId": raw_account_id,
        },
    }

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    pause = await async_client.post(f"/api/accounts/{expected_account_id}/pause")
    assert pause.status_code == 200

    async def fake_compact(_payload, _headers, _access_token, _account_id):
        return OpenAIResponsePayload(id="resp_probe_ok", status="completed")

    monkeypatch.setattr(proxy_service_mod, "core_compact_responses", fake_compact)

    resume = await async_client.post(f"/api/accounts/{expected_account_id}/reactivate")
    assert resume.status_code == 200
    body = resume.json()
    assert body["status"] == "reactivated"
    assert body["probe"]["ok"] is True
    assert body["probe"]["statusCode"] == 200

    async with AccountsSessionLocal() as session:
        account = await session.get(Account, expected_account_id)
        assert account is not None
        assert account.status == AccountStatus.ACTIVE
        assert account.reset_at is None


@pytest.mark.asyncio
async def test_reactivate_probe_failure_keeps_status(async_client, monkeypatch):
    from app.modules.proxy import service as proxy_service_mod

    email = "resume_fail@example.com"
    raw_account_id = "acc_resume_fail"
    payload = {
        "email": email,
        "chatgpt_account_id": raw_account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access",
            "refreshToken": "refresh",
            "accountId": raw_account_id,
        },
    }

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    pause = await async_client.post(f"/api/accounts/{expected_account_id}/pause")
    assert pause.status_code == 200

    async def fake_compact(_payload, _headers, _access_token, _account_id):
        raise ProxyResponseError(
            429,
            {
                "error": {
                    "type": "usage_limit_reached",
                    "message": "limit reached",
                    "plan_type": "plus",
                    "resets_at": 1767612327,
                }
            },
        )

    monkeypatch.setattr(proxy_service_mod, "core_compact_responses", fake_compact)

    resume = await async_client.post(f"/api/accounts/{expected_account_id}/reactivate")
    assert resume.status_code == 409
    body = resume.json()
    assert body["error"]["code"] == "reactivate_probe_failed"
    assert "Probe failed" in body["error"]["message"]
    assert body["error"]["details"]["upstreamStatusCode"] == 429
    assert body["error"]["details"]["resetsAt"]

    async with AccountsSessionLocal() as session:
        account = await session.get(Account, expected_account_id)
        assert account is not None
        assert account.status == AccountStatus.PAUSED


@pytest.mark.asyncio
async def test_reactivate_allows_rate_limited_when_probe_ok(async_client, monkeypatch):
    from app.modules.proxy import service as proxy_service_mod

    email = "resume_limited@example.com"
    raw_account_id = "acc_resume_limited"
    payload = {
        "email": email,
        "chatgpt_account_id": raw_account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access",
            "refreshToken": "refresh",
            "accountId": raw_account_id,
        },
    }

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async with AccountsSessionLocal() as session:
        account = await session.get(Account, expected_account_id)
        assert account is not None
        account.status = AccountStatus.RATE_LIMITED
        account.reset_at = 1999999999
        await session.commit()

    async def fake_compact(_payload, _headers, _access_token, _account_id):
        return OpenAIResponsePayload(id="resp_probe_ok", status="completed")

    monkeypatch.setattr(proxy_service_mod, "core_compact_responses", fake_compact)

    resume = await async_client.post(f"/api/accounts/{expected_account_id}/reactivate")
    assert resume.status_code == 200

    async with AccountsSessionLocal() as session:
        account = await session.get(Account, expected_account_id)
        assert account is not None
        assert account.status == AccountStatus.ACTIVE
        assert account.reset_at is None


@pytest.mark.asyncio
async def test_pause_missing_account_returns_404(async_client):
    response = await async_client.post("/api/accounts/missing/pause")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "account_not_found"


@pytest.mark.asyncio
async def test_pause_account(async_client):
    email = "pause@example.com"
    raw_account_id = "acc_pause"
    payload = {
        "email": email,
        "chatgpt_account_id": raw_account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access",
            "refreshToken": "refresh",
            "accountId": raw_account_id,
        },
    }

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    pause = await async_client.post(f"/api/accounts/{expected_account_id}/pause")
    assert pause.status_code == 200
    assert pause.json()["status"] == "paused"

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = accounts.json()["accounts"]
    matched = next((account for account in data if account["accountId"] == expected_account_id), None)
    assert matched is not None
    assert matched["status"] == "paused"
    assert matched["deactivationReason"] is None


@pytest.mark.asyncio
async def test_delete_missing_account_returns_404(async_client):
    response = await async_client.delete("/api/accounts/missing")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "account_not_found"


@pytest.mark.asyncio
async def test_pin_and_unpin_account_updates_routing_pool(async_client):
    email = "pin@example.com"
    raw_account_id = "acc_pin"
    payload = {
        "email": email,
        "chatgpt_account_id": raw_account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access",
            "refreshToken": "refresh",
            "accountId": raw_account_id,
        },
    }

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    pin = await async_client.post(f"/api/accounts/{expected_account_id}/pin")
    assert pin.status_code == 200
    pinned = pin.json()
    assert pinned["status"] == "pinned"
    assert pinned["pinnedAccountIds"] == [expected_account_id]

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = accounts.json()["accounts"]
    matched = next((account for account in data if account["accountId"] == expected_account_id), None)
    assert matched is not None
    assert matched["pinned"] is True

    unpin = await async_client.post(f"/api/accounts/{expected_account_id}/unpin")
    assert unpin.status_code == 200
    unpinned = unpin.json()
    assert unpinned["status"] == "unpinned"
    assert unpinned["pinnedAccountIds"] == []

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = accounts.json()["accounts"]
    matched = next((account for account in data if account["accountId"] == expected_account_id), None)
    assert matched is not None
    assert matched["pinned"] is False
