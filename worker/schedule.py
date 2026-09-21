"""Post–US-close advisory schedule (spec/decisions/0011-us-close-schedule-offset.md)."""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
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


def marker_path(data_dir: str) -> Path:
    return Path(data_dir) / ".advisory_schedule_last"


def already_fired_today(data_dir: str, now: datetime | None = None) -> bool:
    current = now.astimezone(US_TZ) if now else datetime.now(US_TZ)
    path = marker_path(data_dir)
    if not path.is_file():
        return False
    try:
        stamped = path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return stamped == current.date().isoformat()


def mark_fired_today(data_dir: str, now: datetime | None = None) -> None:
    current = now.astimezone(US_TZ) if now else datetime.now(US_TZ)
    path = marker_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(current.date().isoformat() + "\n", encoding="utf-8")


def schedule_status(data_dir: str, now: datetime | None = None) -> dict:
    current = now.astimezone(US_TZ) if now else datetime.now(US_TZ)
    offset = after_us_close_minutes()
    fire = fire_time_et(current.date())
    return {
        "timezone": "America/New_York",
        "us_close": "16:00",
        "after_us_close_minutes": offset,
        "fire_at_et": fire.strftime("%H:%M"),
        "now_et": current.isoformat(),
        "trading_day": is_trading_day(current.date()),
        "should_fire": should_fire_now(current),
        "already_fired_today": already_fired_today(data_dir, current),
        "check_seconds": schedule_check_seconds(),
    }
