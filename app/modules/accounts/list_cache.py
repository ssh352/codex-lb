from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from app.core.utils.time import utcnow
from app.modules.accounts.schemas import AccountSummary


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    expires_at: datetime
    value: list[AccountSummary]


_LOCK = asyncio.Lock()
_ENTRY: _CacheEntry | None = None
_TTL_SECONDS = 3


def invalidate_accounts_list_cache() -> None:
    global _ENTRY
    _ENTRY = None


async def get_or_build_accounts_list(
    build: Callable[[], Awaitable[list[AccountSummary]]],
) -> list[AccountSummary]:
    global _ENTRY

    now = utcnow()
    entry = _ENTRY
    if entry is not None and entry.expires_at > now:
        return entry.value

    async with _LOCK:
        now = utcnow()
        entry = _ENTRY
        if entry is not None and entry.expires_at > now:
            return entry.value

        value = await build()
        _ENTRY = _CacheEntry(expires_at=utcnow() + timedelta(seconds=_TTL_SECONDS), value=value)
        return value
