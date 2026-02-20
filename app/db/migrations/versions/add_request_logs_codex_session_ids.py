from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


def _request_logs_column_state(session: Session) -> tuple[bool, bool, bool]:
    conn = session.connection()
    inspector = inspect(conn)
    if not inspector.has_table("request_logs"):
        return False, False, False
    columns = {column["name"] for column in inspector.get_columns("request_logs")}
    return True, "codex_session_id" in columns, "codex_conversation_id" in columns


async def run(session: AsyncSession) -> None:
    has_table, has_session_id, has_conversation_id = await session.run_sync(_request_logs_column_state)
    if not has_table:
        return
    # Store the raw Codex header values for personal deployments to make querying trivial.
    if not has_session_id:
        await session.execute(text("ALTER TABLE request_logs ADD COLUMN codex_session_id VARCHAR"))
    if not has_conversation_id:
        await session.execute(text("ALTER TABLE request_logs ADD COLUMN codex_conversation_id VARCHAR"))
