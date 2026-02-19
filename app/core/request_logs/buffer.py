from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

from app.core.config.settings import get_settings
from app.core.metrics import get_metrics
from app.core.utils.request_id import ensure_request_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RequestLogCreate:
    account_id: str
    request_id: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    reasoning_tokens: int | None
    reasoning_effort: str | None
    latency_ms: int | None
    status: str
    error_code: str | None
    error_message: str | None
    prompt_cache_key_hash: str | None
    requested_at: datetime


@dataclass(slots=True)
class RequestLogBuffer:
    enabled: bool
    _queue: asyncio.Queue[RequestLogCreate]

    def try_enqueue(self, entry: RequestLogCreate) -> bool:
        if not self.enabled:
            return False
        try:
            self._queue.put_nowait(entry)
            return True
        except asyncio.QueueFull:
            logger.warning("Request log buffer full; dropping request_id=%s", entry.request_id)
            return False

    def drain(self, max_items: int) -> list[RequestLogCreate]:
        items: list[RequestLogCreate] = []
        for _ in range(max_items):
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items

    def size(self) -> int:
        return self._queue.qsize()


@lru_cache(maxsize=1)
def get_request_log_buffer() -> RequestLogBuffer:
    settings = get_settings()
    maxsize = settings.request_logs_buffer_maxsize
    return RequestLogBuffer(
        enabled=settings.request_logs_buffer_enabled,
        _queue=asyncio.Queue(maxsize=maxsize),
    )


def enqueue_request_log(entry: RequestLogCreate) -> bool:
    normalized = RequestLogCreate(
        account_id=entry.account_id,
        request_id=ensure_request_id(entry.request_id),
        model=entry.model,
        input_tokens=entry.input_tokens,
        output_tokens=entry.output_tokens,
        cached_input_tokens=entry.cached_input_tokens,
        reasoning_tokens=entry.reasoning_tokens,
        reasoning_effort=entry.reasoning_effort,
        latency_ms=entry.latency_ms,
        status=entry.status,
        error_code=entry.error_code,
        error_message=entry.error_message,
        prompt_cache_key_hash=entry.prompt_cache_key_hash,
        requested_at=entry.requested_at,
    )
    ok = get_request_log_buffer().try_enqueue(normalized)
    if not ok:
        get_metrics().inc_request_log_buffer_dropped()
    return ok
