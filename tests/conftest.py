from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="codex-lb-tests-"))
TEST_DB_PATH = TEST_DB_DIR / "codex-lb.db"
TEST_ACCOUNTS_DB_PATH = TEST_DB_DIR / "codex-lb-accounts.db"

os.environ["CODEX_LB_DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB_PATH}"
os.environ["CODEX_LB_ACCOUNTS_DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_ACCOUNTS_DB_PATH}"
os.environ["CODEX_LB_UPSTREAM_BASE_URL"] = "https://example.invalid/backend-api"
os.environ["CODEX_LB_USAGE_REFRESH_ENABLED"] = "false"
os.environ["CODEX_LB_PROXY_SNAPSHOT_TTL_SECONDS"] = "0.0001"
os.environ["CODEX_LB_REQUEST_LOGS_BUFFER_ENABLED"] = "false"

from app.db.models import Account, Base  # noqa: E402
from app.db.session import accounts_engine, engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.modules.accounts.list_cache import invalidate_accounts_list_cache  # noqa: E402
from app.modules.request_logs.options_cache import (  # noqa: E402
    invalidate_request_log_options_cache,
)


async def _reset_databases() -> None:
    main_tables = [table for name, table in Base.metadata.tables.items() if name != Account.__tablename__]
    accounts_table = Base.metadata.tables[Account.__tablename__]
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS schema_migrations"))
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=main_tables))
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=main_tables))
    async with accounts_engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS schema_migrations"))
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=[accounts_table]))
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=[accounts_table]))


@pytest_asyncio.fixture
async def app_instance():
    app = create_app()
    await _reset_databases()
    invalidate_accounts_list_cache()
    invalidate_request_log_options_cache()
    return app


@pytest_asyncio.fixture(scope="session", autouse=True)
async def dispose_engine():
    yield
    await engine.dispose()
    await accounts_engine.dispose()


@pytest_asyncio.fixture
async def db_setup():
    await _reset_databases()
    invalidate_accounts_list_cache()
    invalidate_request_log_options_cache()
    return True


@pytest_asyncio.fixture
async def async_client(app_instance):
    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


@pytest.fixture(autouse=True)
def temp_key_file(monkeypatch):
    key_path = TEST_DB_DIR / f"encryption-{uuid4().hex}.key"
    monkeypatch.setenv("CODEX_LB_ENCRYPTION_KEY_FILE", str(key_path))
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    return key_path
