from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def run(session: AsyncSession) -> None:
    bind = session.get_bind()
    dialect = getattr(getattr(bind, "dialect", None), "name", None)
    if dialect not in {"sqlite", "postgresql"}:
        return

    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_logs_requested_at
            ON request_logs(requested_at DESC)
            """
        )
    )
