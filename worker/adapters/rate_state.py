from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Mapping

from adapters.base import AdapterMetadata
from adapters.errors import AdapterQuotaDeferredError


class PersistentRateLimiter:
    """SQLite-backed pacing and cooldown state shared across worker restarts."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def before_call(self, adapter: object) -> None:
        meta: AdapterMetadata = adapter.metadata  # type: ignore[attr-defined]
        provider = meta.provider
        row = self.conn.execute(
            "SELECT * FROM provider_rate_state WHERE provider = ?", (provider,)
        ).fetchone()
        now = datetime.now(timezone.utc)
        if row is not None and row["cooldown_until"]:
            cooldown = _parse_time(str(row["cooldown_until"]))
            if cooldown and cooldown > now:
                raise AdapterQuotaDeferredError(
                    f"{provider} cooldown until {cooldown.isoformat()}",
                    retry_after=(cooldown - now).total_seconds(),
                )

        minimum = _env_float(
            f"{provider.upper().replace('-', '_')}_MIN_INTERVAL_SECONDS",
            meta.rate_policy.minimum_interval_seconds,
        )
        if row is not None and row["last_call_at"] and minimum > 0:
            previous = _parse_time(str(row["last_call_at"]))
            if previous:
                remaining = minimum - (now - previous).total_seconds()
                if remaining > 0:
                    time.sleep(remaining)
                    now = datetime.now(timezone.utc)

        window_seconds = int(_env_float(
            f"{provider.upper().replace('-', '_')}_RATE_WINDOW_SECONDS",
            meta.rate_policy.window_seconds,
        ))
        window_limit = _env_int(
            f"{provider.upper().replace('-', '_')}_RATE_WINDOW_LIMIT",
            meta.rate_policy.window_limit,
        )
        window_started = _parse_time(str(row["window_started_at"])) if row and row["window_started_at"] else None
        count = int(row["window_count"] or 0) if row else 0
        if window_started is None or (now - window_started).total_seconds() >= window_seconds:
            window_started, count = now, 0
        cost = max(1, meta.rate_policy.request_cost)
        if window_limit is not None and count + cost > window_limit:
            cooldown = window_started + timedelta(seconds=window_seconds)
            self._upsert(provider, None, window_started, count, cooldown)
            self.conn.commit()
            raise AdapterQuotaDeferredError(
                f"{provider} configured quota exhausted until {cooldown.isoformat()}",
                retry_after=max(0.0, (cooldown - now).total_seconds()),
            )
        self._upsert(provider, now, window_started, count + cost, None)
        self.conn.commit()

    def after_call(self, adapter: object) -> None:
        headers = getattr(adapter, "last_response_headers", None)
        if isinstance(headers, Mapping):
            self.observe_headers(adapter.metadata.provider, headers)  # type: ignore[attr-defined]

    def observe_headers(self, provider: str, headers: Mapping[str, object]) -> None:
        normalized = {str(k).lower(): str(v) for k, v in headers.items()}
        limit = _first_int(
            normalized, "x-ratelimit-limit", "x-api-limit",
            "x-usage-limit", "x-usagelimit-limit",
        )
        remaining = _first_int(
            normalized, "x-ratelimit-remaining", "x-api-remaining",
            "x-usage-remaining", "x-usagelimit-remaining"
        )
        reset = _first(normalized, "x-ratelimit-reset", "x-api-reset", "x-usage-reset")
        retry_after = normalized.get("retry-after")
        cooldown = _retry_time(retry_after) if retry_after else None
        if remaining is not None and remaining <= 0 and reset:
            cooldown = _reset_time(reset) or cooldown
        self.conn.execute(
            """
            UPDATE provider_rate_state SET observed_limit = COALESCE(?, observed_limit),
                observed_remaining = COALESCE(?, observed_remaining),
                observed_reset_at = COALESCE(?, observed_reset_at),
                cooldown_until = COALESCE(?, cooldown_until), updated_at = ?
            WHERE provider = ?
            """,
            (limit, remaining, reset, cooldown.isoformat() if cooldown else None,
             datetime.now(timezone.utc).isoformat(), provider),
        )
        self.conn.commit()

    def defer(self, provider: str, retry_after: float | None) -> None:
        seconds = retry_after if retry_after is not None else _env_float(
            f"{provider.upper().replace('-', '_')}_COOLDOWN_SECONDS", 60.0
        )
        cooldown = datetime.now(timezone.utc) + timedelta(seconds=max(1.0, seconds))
        self.conn.execute(
            "UPDATE provider_rate_state SET cooldown_until = ?, updated_at = ? WHERE provider = ?",
            (cooldown.isoformat(), datetime.now(timezone.utc).isoformat(), provider),
        )
        self.conn.commit()

    def _upsert(self, provider: str, last_call_at: datetime | None,
                window_started_at: datetime, window_count: int,
                cooldown_until: datetime | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO provider_rate_state
                (provider, window_started_at, window_count, last_call_at, cooldown_until, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET
                window_started_at=excluded.window_started_at,
                window_count=excluded.window_count,
                last_call_at=COALESCE(excluded.last_call_at, provider_rate_state.last_call_at),
                cooldown_until=COALESCE(excluded.cooldown_until, provider_rate_state.cooldown_until),
                updated_at=excluded.updated_at
            """,
            (provider, window_started_at.isoformat(), window_count,
             last_call_at.isoformat() if last_call_at else None,
             cooldown_until.isoformat() if cooldown_until else None, now),
        )


def _parse_time(value: str) -> datetime | None:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, str(default))))
    except ValueError:
        return default


def _env_int(name: str, default: int | None) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
        return value if value > 0 else None
    except ValueError:
        return default


def _first(headers: Mapping[str, str], *names: str) -> str | None:
    return next((headers[n] for n in names if n in headers), None)


def _first_int(headers: Mapping[str, str], *names: str) -> int | None:
    value = _first(headers, *names)
    try:
        return int(float(value)) if value is not None else None
    except ValueError:
        return None


def _retry_time(value: str) -> datetime | None:
    try:
        return datetime.now(timezone.utc) + timedelta(seconds=max(0.0, float(value)))
    except ValueError:
        try:
            return parsedate_to_datetime(value).astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None


def _reset_time(value: str) -> datetime | None:
    try:
        number = float(value)
        if number > 1_000_000_000:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        return datetime.now(timezone.utc) + timedelta(seconds=max(0.0, number))
    except ValueError:
        return _parse_time(value)
