"""Time helpers. Everything business-facing uses Western Indonesia Time (WIB)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

JAKARTA = ZoneInfo("Asia/Jakarta")


def now_jakarta() -> datetime:
    return datetime.now(JAKARTA)


def current_year() -> int:
    """Current budget year (tahun anggaran) according to the Jakarta calendar."""
    return now_jakarta().year


def parse_hhmm(value: str) -> time:
    """Parse ``HH:MM`` (24h). Raises ValueError on bad input."""
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(p.isascii() and p.isdigit() for p in parts):
        raise ValueError(f"expected HH:MM, got {value!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"time out of range: {value!r}")
    return time(hour, minute)


def next_run_at(now: datetime, at: time) -> datetime:
    """Next occurrence of wall-clock time ``at`` in Jakarta strictly after ``now``."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(JAKARTA)
    candidate = local.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate


def seconds_until(now: datetime, at: time) -> float:
    return (next_run_at(now, at) - now.astimezone(JAKARTA)).total_seconds()
