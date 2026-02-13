from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_serializer
from pydantic.alias_generators import to_camel


class DashboardModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        ser_json_timedelta="iso8601",
    )

    @field_serializer("*", when_used="json")
    def serialize_datetime_as_utc(value, _info):
        # All datetimes in dashboard APIs are serialized as ISO 8601 strings.
        #
        # Convention:
        # - tz-aware datetimes are serialized with their explicit offset (UTC becomes "Z")
        # - tz-naive datetimes are assumed to be UTC and get a trailing "Z"
        #
        # This matches our internal convention where timestamps persisted to SQLite are UTC-naive
        # (see `app/core/utils/time.py:utcnow`) and should be displayed in the user's local timezone
        # by clients (e.g., `new Date(iso)` in the dashboard).
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.isoformat() + "Z"
            return value.isoformat().replace("+00:00", "Z")
        return value
