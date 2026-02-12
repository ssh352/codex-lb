from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from sqlalchemy import insert

from app.core.config.settings import get_settings
from app.core.request_logs.buffer import RequestLogCreate, get_request_log_buffer
from app.db.models import RequestLog
from app.db.session import SessionLocal, _safe_close, _safe_rollback

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RequestLogsFlushScheduler:
    interval_seconds: float
    max_batch: int
    enabled: bool
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> None:
        if not self.enabled:
            return
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        await self.flush_all()

    async def flush_all(self) -> None:
        if not self.enabled:
            return
        async with self._lock:
            await self._flush_until_empty()

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._flush_once()
            except Exception:
                logger.exception("Request log flush failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _flush_once(self) -> None:
        async with self._lock:
            buffer = get_request_log_buffer()
            if buffer.size() == 0:
                return
            await self._flush_batch(buffer.drain(self.max_batch))

    async def _flush_until_empty(self) -> None:
        buffer = get_request_log_buffer()
        while buffer.size() > 0:
            batch = buffer.drain(self.max_batch)
            if not batch:
                return
            await self._flush_batch(batch)

    async def _flush_batch(self, batch: list[RequestLogCreate]) -> None:
        if not batch:
            return
        rows = [
            {
                "account_id": entry.account_id,
                "request_id": entry.request_id,
                "requested_at": entry.requested_at,
                "model": entry.model,
                "input_tokens": entry.input_tokens,
                "output_tokens": entry.output_tokens,
                "cached_input_tokens": entry.cached_input_tokens,
                "reasoning_tokens": entry.reasoning_tokens,
                "reasoning_effort": entry.reasoning_effort,
                "latency_ms": entry.latency_ms,
                "status": entry.status,
                "error_code": entry.error_code,
                "error_message": entry.error_message,
            }
            for entry in batch
        ]
        session = SessionLocal()
        try:
            await session.execute(insert(RequestLog), rows)
            await session.commit()
        except Exception:
            if session.in_transaction():
                await _safe_rollback(session)
            raise
        finally:
            await _safe_close(session)


def build_request_logs_flush_scheduler() -> RequestLogsFlushScheduler:
    settings = get_settings()
    return RequestLogsFlushScheduler(
        interval_seconds=settings.request_logs_flush_interval_seconds,
        max_batch=settings.request_logs_flush_max_batch,
        enabled=settings.request_logs_buffer_enabled,
    )
