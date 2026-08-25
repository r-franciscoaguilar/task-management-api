from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """Timezone-aware UTC now.

    Used as the default for every timestamp column so stored values are
    unambiguous rather than naive local time.
    """
    return datetime.now(timezone.utc)


class UtcDateTime(TypeDecorator):
    """A DateTime that always round-trips as timezone-aware UTC.

    SQLite has no native timezone support, so a value written through a plain
    DateTime(timezone=True) comes back naive and would serialize into API
    responses as an ambiguous bare timestamp. This normalizes to UTC on write
    and re-attaches UTC on read, so clients always receive an explicit offset.
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
