from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import AsyncIterator, Awaitable, TypeVar

import anyio
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql import text

from app.core.config.settings import get_settings
from app.db.migrations import run_migrations
from app.db.sqlite_utils import check_sqlite_integrity, sqlite_db_path_from_url

_settings = get_settings()

logger = logging.getLogger(__name__)

_SQLITE_BUSY_TIMEOUT_MS = 5_000
_SQLITE_BUSY_TIMEOUT_SECONDS = _SQLITE_BUSY_TIMEOUT_MS / 1000


def _is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite+aiosqlite:///") or url.startswith("sqlite:///")


def _is_sqlite_memory_url(url: str) -> bool:
    return _is_sqlite_url(url) and ":memory:" in url


def _configure_sqlite_engine(engine: Engine, *, enable_wal: bool) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: sqlite3.Connection, _: object) -> None:
        cursor: sqlite3.Cursor = dbapi_connection.cursor()
        try:
            if enable_wal:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        finally:
            cursor.close()

_MAIN_DATABASE_URL = _settings.database_url
_ACCOUNTS_DATABASE_URL = _settings.accounts_database_url


def _build_engine(url: str) -> AsyncEngine:
    if _is_sqlite_url(url):
        is_sqlite_memory = _is_sqlite_memory_url(url)
        if is_sqlite_memory:
            engine = create_async_engine(
                url,
                echo=False,
                connect_args={"timeout": _SQLITE_BUSY_TIMEOUT_SECONDS},
            )
        else:
            engine = create_async_engine(
                url,
                echo=False,
                pool_size=_settings.database_pool_size,
                max_overflow=_settings.database_max_overflow,
                pool_timeout=_settings.database_pool_timeout_seconds,
                connect_args={"timeout": _SQLITE_BUSY_TIMEOUT_SECONDS},
            )
        _configure_sqlite_engine(engine.sync_engine, enable_wal=not is_sqlite_memory)
        return engine

    return create_async_engine(
        url,
        echo=False,
        pool_size=_settings.database_pool_size,
        max_overflow=_settings.database_max_overflow,
        pool_timeout=_settings.database_pool_timeout_seconds,
    )


engine = _build_engine(_MAIN_DATABASE_URL)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

accounts_engine = _build_engine(_ACCOUNTS_DATABASE_URL)
AccountsSessionLocal = async_sessionmaker(accounts_engine, expire_on_commit=False, class_=AsyncSession)

_T = TypeVar("_T")


def _ensure_sqlite_dir(url: str) -> None:
    if not (url.startswith("sqlite+aiosqlite:") or url.startswith("sqlite:")):
        return

    marker = ":///"
    marker_index = url.find(marker)
    if marker_index < 0:
        return

    # Works for both relative (sqlite+aiosqlite:///./db.sqlite) and absolute
    # paths (sqlite+aiosqlite:////var/lib/app/db.sqlite).
    path = url[marker_index + len(marker) :]
    path = path.partition("?")[0]
    path = path.partition("#")[0]

    if not path or path == ":memory:":
        return

    Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)


async def _shielded(awaitable: Awaitable[_T]) -> _T:
    with anyio.CancelScope(shield=True):
        return await awaitable


async def _safe_rollback(session: AsyncSession) -> None:
    if not session.in_transaction():
        return
    try:
        await _shielded(session.rollback())
    except BaseException:
        return


async def _safe_close(session: AsyncSession) -> None:
    try:
        await _shielded(session.close())
    except BaseException:
        return


async def get_session() -> AsyncIterator[AsyncSession]:
    session = SessionLocal()
    try:
        yield session
    except BaseException:
        await _safe_rollback(session)
        raise
    finally:
        if session.in_transaction():
            await _safe_rollback(session)
        await _safe_close(session)


async def get_accounts_session() -> AsyncIterator[AsyncSession]:
    session = AccountsSessionLocal()
    try:
        yield session
    except BaseException:
        await _safe_rollback(session)
        raise
    finally:
        if session.in_transaction():
            await _safe_rollback(session)
        await _safe_close(session)


async def _warn_if_legacy_accounts_present() -> None:
    if not (_is_sqlite_url(_MAIN_DATABASE_URL) and _is_sqlite_url(_ACCOUNTS_DATABASE_URL)):
        return
    async with SessionLocal() as main_session, AccountsSessionLocal() as accounts_session:
        main_has_accounts = (
            await main_session.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='accounts'"),
            )
        ).scalar_one_or_none()
        if main_has_accounts is None:
            return
        try:
            main_count = int((await main_session.execute(text("SELECT COUNT(*) FROM accounts"))).scalar_one() or 0)
        except Exception:
            return
        try:
            accounts_count = int(
                (await accounts_session.execute(text("SELECT COUNT(*) FROM accounts"))).scalar_one() or 0
            )
        except Exception:
            return
        if main_count > 0 and accounts_count == 0:
            logger.warning(
                "Legacy accounts table found in main store DB, but accounts DB is empty. "
                "Run `codex-lb migrate-accounts` to copy accounts into accounts.db."
            )


async def migrate_accounts_from_main_to_accounts_db(*, drop_legacy: bool = False) -> int:
    if not (_is_sqlite_url(_MAIN_DATABASE_URL) and _is_sqlite_url(_ACCOUNTS_DATABASE_URL)):
        raise RuntimeError("Accounts DB migration is only supported for SQLite URLs")

    async with SessionLocal() as main_session, AccountsSessionLocal() as accounts_session:
        main_has_accounts = (
            await main_session.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='accounts'"),
            )
        ).scalar_one_or_none()
        if main_has_accounts is None:
            return 0

        accounts_count = (
            await accounts_session.execute(
                text("SELECT COUNT(*) FROM accounts"),
            )
        ).scalar_one()
        if int(accounts_count) > 0:
            raise RuntimeError("Refusing to migrate: accounts DB already has accounts rows")

        main_count = (
            await main_session.execute(
                text("SELECT COUNT(*) FROM accounts"),
            )
        ).scalar_one()
        if int(main_count) <= 0:
            if drop_legacy:
                await main_session.execute(text("DROP TABLE accounts"))
                await main_session.commit()
                logger.info("Dropped legacy accounts table from main store DB (empty)")
            return 0

        info = await main_session.execute(text("PRAGMA table_info(accounts)"))
        main_columns = {row[1] for row in info.fetchall() if len(row) > 1}
        desired = [
            "id",
            "chatgpt_account_id",
            "email",
            "plan_type",
            "access_token_encrypted",
            "refresh_token_encrypted",
            "id_token_encrypted",
            "last_refresh",
            "created_at",
            "status",
            "deactivation_reason",
            "reset_at",
        ]
        selected = [col for col in desired if col in main_columns]
        if not selected:
            return 0

        rows = (
            await main_session.execute(
                text(f"SELECT {', '.join(selected)} FROM accounts"),
            )
        ).mappings().all()
        if not rows:
            return 0

        from app.core.auth import DEFAULT_PLAN
        from app.db.models import AccountStatus

        inserts: list[dict[str, object]] = []
        for row in rows:
            record: dict[str, object] = dict(row)
            if "plan_type" not in record or not record.get("plan_type"):
                record["plan_type"] = DEFAULT_PLAN
            if "status" not in record or not record.get("status"):
                record["status"] = AccountStatus.ACTIVE.value
            inserts.append(record)

        if not inserts:
            return 0

        columns = sorted({key for record in inserts for key in record.keys()})
        cols_csv = ", ".join(columns)
        placeholders = ", ".join(f":{name}" for name in columns)
        await accounts_session.execute(
            text(f"INSERT INTO accounts ({cols_csv}) VALUES ({placeholders})"),
            [{name: record.get(name) for name in columns} for record in inserts],
        )
        await accounts_session.commit()
        migrated = len(inserts)
        logger.info("Migrated accounts from main store DB into accounts DB count=%s", migrated)

        if drop_legacy:
            await main_session.execute(text("DROP TABLE accounts"))
            await main_session.commit()
            logger.info("Dropped legacy accounts table from main store DB")

        return migrated


async def init_db() -> None:
    from app.db.models import Base

    _ensure_sqlite_dir(_MAIN_DATABASE_URL)
    _ensure_sqlite_dir(_ACCOUNTS_DATABASE_URL)

    for url in (_MAIN_DATABASE_URL, _ACCOUNTS_DATABASE_URL):
        sqlite_path = sqlite_db_path_from_url(url)
        if sqlite_path is None:
            continue
        integrity = check_sqlite_integrity(sqlite_path)
        if integrity.ok:
            continue
        details = integrity.details or "unknown error"
        logger.error("SQLite integrity check failed path=%s details=%s", sqlite_path, details)
        if "locked" in details.lower():
            message = f"SQLite integrity check failed for {sqlite_path} ({details}). Another instance may be running."
        else:
            message = (
                f"SQLite integrity check failed for {sqlite_path} ({details}). "
                "The database appears corrupted or the filesystem is unhealthy. "
                "Stop the app and run "
                f'`python -m app.db.recover --db "{sqlite_path}" --replace` '
                "or restore a backup."
            )
        raise RuntimeError(message)

    from app.db.models import Account

    main_tables = [table for name, table in Base.metadata.tables.items() if name != Account.__tablename__]
    accounts_table = Base.metadata.tables[Account.__tablename__]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=main_tables))

    async with accounts_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=[accounts_table]))

    await _warn_if_legacy_accounts_present()

    async with SessionLocal() as session:
        try:
            updated = await run_migrations(session, role="main")
            if updated:
                logger.info("Applied main database migrations count=%s", updated)
        except Exception:
            logger.exception("Failed to apply main database migrations")
            if get_settings().database_migrations_fail_fast:
                raise

    async with AccountsSessionLocal() as session:
        try:
            updated = await run_migrations(session, role="accounts")
            if updated:
                logger.info("Applied accounts database migrations count=%s", updated)
        except Exception:
            logger.exception("Failed to apply accounts database migrations")
            if get_settings().database_migrations_fail_fast:
                raise


async def close_db() -> None:
    await engine.dispose()
    await accounts_engine.dispose()
