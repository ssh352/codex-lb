from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import get_settings


async def run(session: AsyncSession) -> None:
    settings = get_settings()
    if settings.accounts_database_url == settings.database_url:
        return

    bind = session.get_bind()
    dialect = getattr(getattr(bind, "dialect", None), "name", None)
    if dialect != "sqlite":
        return

    await session.execute(text("PRAGMA foreign_keys=OFF"))
    try:
        await _sqlite_rebuild_without_foreign_keys(session, "usage_history")
        await _sqlite_rebuild_without_foreign_keys(session, "request_logs")
        await _sqlite_rebuild_without_foreign_keys(session, "sticky_sessions")
        await _ensure_indexes(session)
    finally:
        await session.execute(text("PRAGMA foreign_keys=ON"))


async def _sqlite_rebuild_without_foreign_keys(session: AsyncSession, table: str) -> None:
    result = await session.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),
        {"name": table},
    )
    create_sql = result.scalar_one_or_none()
    if not create_sql:
        return

    fk_rows = await session.execute(text(f"PRAGMA foreign_key_list({table})"))
    foreign_keys = fk_rows.fetchall()
    if not foreign_keys:
        return

    info_rows = await session.execute(text(f"PRAGMA table_info({table})"))
    columns = info_rows.fetchall()
    if not columns:
        return

    tmp_table = f"{table}__no_fk"
    await session.execute(text(f"DROP TABLE IF EXISTS {tmp_table}"))

    column_defs: list[str] = []
    pk_cols: list[tuple[int, str]] = []
    for row in columns:
        # PRAGMA table_info: cid, name, type, notnull, dflt_value, pk
        name = row[1]
        col_type = row[2] or ""
        notnull = bool(row[3])
        default = row[4]
        pk = int(row[5] or 0)
        if pk:
            pk_cols.append((pk, name))

        clause = f"{name} {col_type}".strip()
        if notnull:
            clause += " NOT NULL"
        if default is not None:
            clause += f" DEFAULT {default}"
        column_defs.append(clause)

    pk_cols_sorted = [name for _, name in sorted(pk_cols, key=lambda x: x[0])]
    if len(pk_cols_sorted) == 1:
        # Append PRIMARY KEY to the column definition.
        pk_name = pk_cols_sorted[0]
        for i, definition in enumerate(column_defs):
            if definition.split(" ", 1)[0] == pk_name:
                column_defs[i] = f"{definition} PRIMARY KEY"
                break
    elif len(pk_cols_sorted) > 1:
        column_defs.append(f"PRIMARY KEY ({', '.join(pk_cols_sorted)})")

    await session.execute(text(f"CREATE TABLE {tmp_table} ({', '.join(column_defs)})"))
    col_names = [row[1] for row in columns]
    cols_csv = ", ".join(col_names)
    await session.execute(text(f"INSERT INTO {tmp_table} ({cols_csv}) SELECT {cols_csv} FROM {table}"))
    await session.execute(text(f"DROP TABLE {table}"))
    await session.execute(text(f"ALTER TABLE {tmp_table} RENAME TO {table}"))


async def _ensure_indexes(session: AsyncSession) -> None:
    await session.execute(text("CREATE INDEX IF NOT EXISTS idx_usage_recorded_at ON usage_history (recorded_at)"))
    await session.execute(
        text("CREATE INDEX IF NOT EXISTS idx_usage_account_time ON usage_history (account_id, recorded_at)")
    )
    await session.execute(
        text("CREATE INDEX IF NOT EXISTS idx_logs_account_time ON request_logs (account_id, requested_at)")
    )
    await session.execute(text("CREATE INDEX IF NOT EXISTS idx_sticky_account ON sticky_sessions (account_id)"))
