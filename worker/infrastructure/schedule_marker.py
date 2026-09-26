"""Persisted once-per-day marker for the post–US-close advisory (spec/decisions/0011-us-close-schedule-offset.md)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from domain.schedule import (
    US_TZ,
    after_us_close_minutes,
    fire_time_et,
    is_trading_day,
    schedule_check_seconds,
    should_fire_now,
)


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
