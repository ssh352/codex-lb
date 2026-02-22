from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from app.core.utils.time import utcnow
from app.modules.request_logs.types import RequestLogFilterOptions


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    expires_at: datetime
    value: RequestLogFilterOptions


_LOCK = asyncio.Lock()
_ENTRIES: dict[str, _CacheEntry] = {}
_TTL_SECONDS = 30


def invalidate_request_log_options_cache() -> None:
    _ENTRIES.clear()


def _build_key(*, status: list[str] | None, since: datetime | None, until: datetime | None) -> str:
    normalized_status = sorted({value.strip().lower() for value in (status or []) if value and value.strip()})
    since_key = since.isoformat() if since else ""
    until_key = until.isoformat() if until else ""
    return f"status={','.join(normalized_status)}|since={since_key}|until={until_key}"


async def get_or_build_request_log_options(
    *,
    status: list[str] | None,
    since: datetime | None,
    until: datetime | None,
    build: Callable[[], Awaitable[RequestLogFilterOptions]],
) -> RequestLogFilterOptions:
    key = _build_key(status=status, since=since, until=until)
    now = utcnow()
    entry = _ENTRIES.get(key)
    if entry is not None and entry.expires_at > now:
        return entry.value

    async with _LOCK:
        now = utcnow()
        entry = _ENTRIES.get(key)
        if entry is not None and entry.expires_at > now:
            return entry.value
        value = await build()
        _ENTRIES[key] = _CacheEntry(expires_at=utcnow() + timedelta(seconds=_TTL_SECONDS), value=value)
        return value
