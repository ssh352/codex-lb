from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc


def dt_from_epoch_seconds(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), tz=UTC)


def iso_utc_from_epoch_seconds(value: int | None) -> str | None:
    dt = dt_from_epoch_seconds(value)
    return dt.isoformat() if dt is not None else None


def now_epoch() -> int:
    return int(time.time())


def format_epoch_and_iso(value: int | None) -> str:
    if value is None:
        return "null"
    return f"{int(value)} ({iso_utc_from_epoch_seconds(int(value))})"


def dt_from_sqlite(value: str) -> datetime:
    # codex-lb DB stores timestamps like "2026-02-19 02:05:11.026906" (no TZ).
    # Treat as UTC because reset_at is epoch-UTC and comparisons are within the same DB.
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _iso_to_dt(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z") and "+" not in raw and "-" not in raw[10:]:
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def parse_time_to_epoch_seconds(value: str) -> int:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Empty time value")
    if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
        return int(raw)
    return int(_iso_to_dt(raw).timestamp())


def redact(value: str, *, keep_prefix: int = 0) -> str:
    raw = (value or "").strip()
    if not raw:
        return raw
    if keep_prefix > 0:
        return raw[:keep_prefix] + "<redacted>"
    return "<redacted>"


def sqlite_connect(path: Path, *, must_exist: bool = True) -> sqlite3.Connection:
    if must_exist and not path.exists():
        raise FileNotFoundError(f"DB not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn
