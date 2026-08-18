from __future__ import annotations

from datetime import UTC, datetime


def as_aware(value: datetime | None) -> datetime | None:
    """Attach UTC to a naive datetime.

    Columns are declared `DateTime(timezone=True)`, but SQLite (used by the
    test suite) discards the offset and hands back naive values. Comparing
    those against an aware `datetime.now(UTC)` raises, so every read of a
    stored timestamp goes through here.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
