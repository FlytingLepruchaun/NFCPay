"""Shared time helpers for the application's naive UTC database timestamps."""

from datetime import datetime, timezone


def utcnow_naive() -> datetime:
    """Return the current UTC time without timezone metadata for DB columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
