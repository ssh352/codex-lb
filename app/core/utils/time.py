from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    # codex-lb stores timestamps as "UTC-naive" datetimes (tzinfo stripped) for simplicity with
    # SQLite + SQLAlchemy. Treat any tz-naive timestamp emitted by the app as UTC, and only apply
    # local timezone conversion at the presentation layer (UI/CLI).
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def from_epoch_seconds(value: int | float | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)
