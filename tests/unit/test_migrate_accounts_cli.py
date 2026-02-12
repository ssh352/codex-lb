from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_migrate_accounts_cli_copies_and_can_drop_legacy(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    main_db = tmp_path / "store.db"
    accounts_db = tmp_path / "accounts.db"
    key_file = tmp_path / "encryption.key"

    with sqlite3.connect(str(main_db)) as conn:
        conn.execute(
            """
            CREATE TABLE accounts (
                id VARCHAR PRIMARY KEY,
                chatgpt_account_id VARCHAR,
                email VARCHAR NOT NULL UNIQUE,
                plan_type VARCHAR NOT NULL,
                access_token_encrypted BLOB NOT NULL,
                refresh_token_encrypted BLOB NOT NULL,
                id_token_encrypted BLOB NOT NULL,
                last_refresh DATETIME NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status VARCHAR NOT NULL,
                deactivation_reason TEXT,
                reset_at INTEGER
            )
            """
        )
        conn.execute(
            """
            INSERT INTO accounts (
                id,
                chatgpt_account_id,
                email,
                plan_type,
                access_token_encrypted,
                refresh_token_encrypted,
                id_token_encrypted,
                last_refresh,
                status,
                deactivation_reason,
                reset_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "acc_cli",
                "acc_cli",
                "cli@example.com",
                "plus",
                b"access",
                b"refresh",
                b"id",
                "2026-01-01 00:00:00",
                "active",
                None,
                None,
            ),
        )
        conn.commit()

    env = os.environ.copy()
    env["CODEX_LB_DATABASE_URL"] = f"sqlite+aiosqlite:///{main_db}"
    env["CODEX_LB_ACCOUNTS_DATABASE_URL"] = f"sqlite+aiosqlite:///{accounts_db}"
    env["CODEX_LB_ENCRYPTION_KEY_FILE"] = str(key_file)
    env["CODEX_LB_UPSTREAM_BASE_URL"] = "https://example.invalid/backend-api"
    env["CODEX_LB_USAGE_REFRESH_ENABLED"] = "false"

    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "migrate-accounts", "--drop-legacy"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "migrated_accounts=1" in (result.stdout or "")

    with sqlite3.connect(str(accounts_db)) as conn:
        row = conn.execute("SELECT id, email FROM accounts WHERE id='acc_cli'").fetchone()
        assert row == ("acc_cli", "cli@example.com")

    with sqlite3.connect(str(main_db)) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'",
        ).fetchone()
        assert row is None
