"""Post–US-close advisory schedule (spec/decisions/0011-us-close-schedule-offset.md)."""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

# America/New_York; 16:00 regular close + offset minutes.
US_TZ = ZoneInfo("America/New_York")
US_CLOSE = time(16, 0)
DEFAULT_AFTER_CLOSE_MINUTES = 30
DEFAULT_CHECK_SECONDS = 60

# NYSE full-day closed (not early closes). Extend yearly as needed.
NYSE_CLOSED_DAYS: frozenset[date] = frozenset(
    {
        # 2025
        date(2025, 1, 1),
        date(2025, 1, 20),
        date(2025, 2, 17),
        date(2025, 4, 18),
        date(2025, 5, 26),
        date(2025, 6, 19),
        date(2025, 7, 4),
        date(2025, 9, 1),
        date(2025, 11, 27),
        date(2025, 12, 25),
        # 2026
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),  # Independence Day observed
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
        # 2027
        date(2027, 1, 1),
        date(2027, 1, 18),
        date(2027, 2, 15),
        date(2027, 3, 26),
        date(2027, 5, 31),
        date(2027, 6, 18),  # Juneteenth observed
        date(2027, 7, 5),  # Independence Day observed
        date(2027, 9, 6),
        date(2027, 11, 25),
        date(2027, 12, 24),  # Christmas observed
    }
)


def after_us_close_minutes() -> int:
    raw = (os.environ.get("ADVISORY_AFTER_US_CLOSE_MINUTES") or str(DEFAULT_AFTER_CLOSE_MINUTES)).strip()
    try:
        return max(0, min(24 * 60, int(raw)))
    except ValueError:
        return DEFAULT_AFTER_CLOSE_MINUTES


def schedule_check_seconds() -> int:
    raw = (os.environ.get("ADVISORY_SCHEDULE_CHECK_SECONDS") or str(DEFAULT_CHECK_SECONDS)).strip()
    try:
        return max(15, int(raw))
    except ValueError:
        return DEFAULT_CHECK_SECONDS


def fire_time_et(day: date | None = None) -> datetime:
    """Return today's (or given day's) fire instant in US/Eastern as aware datetime."""
    d = day or datetime.now(US_TZ).date()
    base = datetime.combine(d, US_CLOSE, tzinfo=US_TZ)
    return base + timedelta(minutes=after_us_close_minutes())


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d not in NYSE_CLOSED_DAYS


def should_fire_now(now: datetime | None = None) -> bool:
    """True when ET clock is on/after fire time on a trading day."""
    current = now.astimezone(US_TZ) if now else datetime.now(US_TZ)
    if not is_trading_day(current.date()):
        return False
    return current >= fire_time_et(current.date())
