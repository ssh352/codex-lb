from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_split_accounts_db_uses_separate_sqlite_files(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    main_db = tmp_path / "main.db"
    accounts_db = tmp_path / "accounts.db"
    key_file = tmp_path / "encryption.key"

    env = os.environ.copy()
    env["CODEX_LB_DATABASE_URL"] = f"sqlite+aiosqlite:///{main_db}"
    env["CODEX_LB_ACCOUNTS_DATABASE_URL"] = f"sqlite+aiosqlite:///{accounts_db}"
    env["CODEX_LB_ENCRYPTION_KEY_FILE"] = str(key_file)
    env["CODEX_LB_UPSTREAM_BASE_URL"] = "https://example.invalid/backend-api"
    env["CODEX_LB_USAGE_REFRESH_ENABLED"] = "false"

    code = r"""
import asyncio
import sqlite3

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import AccountsSessionLocal, SessionLocal, close_db, init_db
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository


async def main() -> None:
    await init_db()

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_split",
        email="split@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )

    async with AccountsSessionLocal() as session:
        await AccountsRepository(session).upsert(account)

    async with SessionLocal() as session:
        await RequestLogsRepository(session).add_log(
            account_id="acc_split",
            request_id="req_split",
            model="gpt-5.2",
            input_tokens=1,
            output_tokens=2,
            latency_ms=10,
            status="success",
            error_code=None,
        )

    await close_db()


asyncio.run(main())
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout

    with sqlite3.connect(str(main_db)) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'",
        ).fetchone()
        assert row is None

    with sqlite3.connect(str(accounts_db)) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'",
        ).fetchone()
        assert row is not None
