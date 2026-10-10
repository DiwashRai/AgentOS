"""Time helpers (pure). The database stores timestamps as ISO-8601 UTC text in one fixed format,
so they sort lexicographically: `2026-10-10T12:00:00.123456+00:00`."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_db(dt: datetime) -> str:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"naive datetime {dt!r}: use an aware datetime (clock.utcnow())")
    return dt.astimezone(UTC).isoformat(timespec="microseconds")


def from_db(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp {value!r} has no UTC offset")
    return dt


def from_db_opt(value: str | None) -> datetime | None:
    return None if value is None else from_db(value)
