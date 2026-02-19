from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Final, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.migrations.versions import (
    add_accounts_chatgpt_account_id,
    add_accounts_reset_at,
    add_dashboard_settings,
    add_dashboard_settings_pins,
    add_dashboard_settings_totp,
    add_request_logs_prompt_cache_key_hash,
    add_request_logs_reasoning_effort,
    add_usage_history_window_index,
    normalize_account_plan_types,
    remove_main_db_account_foreign_keys,
)

_CREATE_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""

_INSERT_MIGRATION = """
INSERT INTO schema_migrations (name, applied_at)
VALUES (:name, :applied_at)
ON CONFLICT(name) DO NOTHING
RETURNING name
"""


@dataclass(frozen=True)
class Migration:
    name: str
    scope: Literal["main", "accounts", "both"]
    run: Callable[[AsyncSession], Awaitable[None]]


MIGRATIONS: Final[tuple[Migration, ...]] = (
    Migration("001_normalize_account_plan_types", "accounts", normalize_account_plan_types.run),
    Migration("002_add_request_logs_reasoning_effort", "main", add_request_logs_reasoning_effort.run),
    Migration("003_add_accounts_reset_at", "accounts", add_accounts_reset_at.run),
    Migration("004_add_accounts_chatgpt_account_id", "accounts", add_accounts_chatgpt_account_id.run),
    Migration("005_add_dashboard_settings", "main", add_dashboard_settings.run),
    Migration("006_add_dashboard_settings_totp", "main", add_dashboard_settings_totp.run),
    Migration("007_add_usage_history_window_index", "main", add_usage_history_window_index.run),
    Migration("008_remove_main_db_account_foreign_keys", "main", remove_main_db_account_foreign_keys.run),
    Migration("009_add_dashboard_settings_pins", "main", add_dashboard_settings_pins.run),
    Migration("010_add_request_logs_prompt_cache_key_hash", "main", add_request_logs_prompt_cache_key_hash.run),
)


async def run_migrations(session: AsyncSession, *, role: Literal["single", "main", "accounts"] = "single") -> int:
    await _ensure_schema_migrations(session)
    if role == "single":
        migrations = MIGRATIONS
    elif role == "main":
        migrations = tuple(entry for entry in MIGRATIONS if entry.scope in {"main", "both"})
    elif role == "accounts":
        migrations = tuple(entry for entry in MIGRATIONS if entry.scope in {"accounts", "both"})
    else:
        raise ValueError("role must be 'single', 'main', or 'accounts'")

    applied_count = 0
    for migration in migrations:
        applied_now = await _apply_migration(session, migration)
        if applied_now:
            applied_count += 1
    return applied_count


async def _apply_migration(session: AsyncSession, migration: Migration) -> bool:
    async with _migration_transaction(session):
        result = await session.execute(
            text(_INSERT_MIGRATION),
            {
                "name": migration.name,
                "applied_at": _utcnow_iso(),
            },
        )
        inserted = result.scalar_one_or_none()
        if inserted is None:
            return False
        await migration.run(session)
    return True


async def _ensure_schema_migrations(session: AsyncSession) -> None:
    async with _migration_transaction(session):
        await session.execute(text(_CREATE_MIGRATIONS_TABLE))


@asynccontextmanager
async def _migration_transaction(session: AsyncSession):
    if session.in_transaction():
        async with session.begin_nested():
            yield
    else:
        async with session.begin():
            yield


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
