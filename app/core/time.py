from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """Timezone-aware UTC now; the default for every timestamp column."""
    return datetime.now(timezone.utc)


class UtcDateTime(TypeDecorator):
    """A DateTime that round-trips as timezone-aware UTC.

    SQLite has no native timezone support, so values written through a plain
    DateTime(timezone=True) come back naive and would serialize as ambiguous
    bare timestamps.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Any
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        # Stored naive-but-UTC, which every backend handles consistently.
        return value.replace(tzinfo=None)

    def process_result_value(
        self, value: datetime | None, dialect: Any
    ) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)
