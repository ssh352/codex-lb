from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


def _dashboard_settings_column_state(session: Session) -> tuple[bool, bool]:
    conn = session.connection()
    inspector = inspect(conn)
    if not inspector.has_table("dashboard_settings"):
        return False, False
    columns = {column["name"] for column in inspector.get_columns("dashboard_settings")}
    return True, "pinned_account_ids_json" in columns


async def run(session: AsyncSession) -> None:
    has_table, has_column = await session.run_sync(_dashboard_settings_column_state)
    if not has_table or has_column:
        return
    await session.execute(text("ALTER TABLE dashboard_settings ADD COLUMN pinned_account_ids_json TEXT"))

