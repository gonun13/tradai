from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from adapters.base import CandidateResult
from adapters import HistoricalPoint, LongHistory

BARS_REQUIRED = 127
TECHNICAL_FIELDS = (
    "rsi_14", "sma_20", "sma_50", "return_1m_pct", "return_3m_pct",
    "return_6m_pct", "vs_sma20_pct", "vs_sma50_pct",
)
EQUITY_GROUPS = {
    "valuation": ("market_cap", "pe_ratio", "price_to_book", "enterprise_value"),
    "profitability": ("profit_margin", "operating_margin", "roe", "roa"),
    "growth": ("revenue_growth", "earnings_growth"),
    "leverage_liquidity": ("debt_to_equity", "current_ratio", "quick_ratio"),
}


def evaluate_quote(provider: str, quote: Any) -> CandidateResult:
    required = {
        "price": getattr(quote, "price", None),
        "currency": getattr(quote, "currency", None),
        "timestamp": getattr(quote, "as_of", None),
    }
    missing = [key for key, value in required.items() if not _present(value)]
    if _number(required["price"]) is None or float(required["price"] or 0) <= 0:
        if "price" not in missing:
            missing.append("price")
    score = round((3 - len(missing)) / 3, 4)
    return CandidateResult(provider, quote, not missing, score, missing, required["timestamp"])


def evaluate_bars(provider: str, bars: Iterable[Any]) -> CandidateResult:
    by_date: dict[str, Any] = {}
    for bar in bars:
        date = str(_value(bar, "bar_date") or "")
        close = _number(_value(bar, "close"))
        if date and close is not None and close > 0:
            by_date[date] = bar
    normalized = [by_date[key] for key in sorted(by_date)]
    count = len(normalized)
    missing = [] if count >= BARS_REQUIRED else [f"daily_closes:{count}/{BARS_REQUIRED}"]
    field_total = max(1, count * 4)
    ohlcv = sum(
        1 for bar in normalized
        for field in ("open", "high", "low", "volume")
        if _number(_value(bar, field)) is not None
    )
    close_score = min(1.0, count / BARS_REQUIRED)
    score = round(close_score * 0.8 + (ohlcv / field_total) * 0.2, 4)
    as_of = str(_value(normalized[-1], "bar_date") or "") if normalized else None
    return CandidateResult(provider, normalized, not missing, score, missing, as_of)


def compact_long_history(points: Iterable[Any]) -> list[HistoricalPoint]:
    """Keep daily/weekly/monthly adjusted closes at 1y and 5y calendar boundaries."""
    valid: dict[date, float] = {}
    for point in points:
        try:
            point_date = date.fromisoformat(str(_value(point, "point_date") or ""))
            value = float(_value(point, "adjusted_close"))
        except (TypeError, ValueError):
            continue
        if value > 0 and math.isfinite(value):
            valid[point_date] = value
    if not valid:
        return []

    latest = max(valid)
    one_year = _years_before(latest, 1)
    five_years = _years_before(latest, 5)
    selected: dict[date, str] = {}
    weekly: dict[tuple[int, int], date] = {}
    monthly: dict[tuple[int, int], date] = {}
    for point_date in sorted(valid):
        if point_date >= one_year:
            selected[point_date] = "daily"
        elif point_date >= five_years:
            iso = point_date.isocalendar()
            weekly[(iso.year, iso.week)] = point_date
        else:
            monthly[(point_date.year, point_date.month)] = point_date
    selected.update({point_date: "weekly" for point_date in weekly.values()})
    selected.update({point_date: "monthly" for point_date in monthly.values()})
    return [
        HistoricalPoint(point_date.isoformat(), valid[point_date], selected[point_date])
        for point_date in sorted(selected)
    ]


def evaluate_long_history(provider: str, history: LongHistory) -> CandidateResult:
    compacted = compact_long_history(history.points)
    missing: list[str] = []
    if len(compacted) < 2:
        missing.append(f"adjusted_closes:{len(compacted)}/2")
    as_of = compacted[-1].point_date if compacted else None
    payload = LongHistory(compacted, str(history.currency or "").upper(), as_of)
    return CandidateResult(
        provider, payload, not missing, 1.0 if not missing else len(compacted) / 2,
        missing, as_of,
    )


def long_history_cache_wins(
    existing_earliest: str | None,
    existing_as_of: str | None,
    candidate: CandidateResult,
) -> bool:
    """Reject a response that truncates either edge of an already accepted full history."""
    payload = candidate.payload
    points = getattr(payload, "points", [])
    if not candidate.complete or not points:
        return existing_earliest is not None
    candidate_earliest = str(points[0].point_date)
    candidate_latest = str(points[-1].point_date)
    return bool(
        existing_earliest
        and existing_as_of
        and (candidate_earliest > existing_earliest or candidate_latest < existing_as_of)
    )


def _years_before(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:  # 29 February -> 28 February.
        return value.replace(year=value.year - years, day=28)


def evaluate_fundamentals(provider: str, payload: dict[str, Any], kind: str) -> CandidateResult:
    missing: list[str] = []
    identity = ("name", "symbol")
    missing.extend(key for key in identity if not _present(payload.get(key)))
    present = sum(1 for key in identity if _present(payload.get(key)))
    total = len(identity)
    if kind == "etf":
        aum_expense = ("aum", "expense_ratio")
        allocation = ("holdings", "allocations")
        total += 2
        if not any(_present(payload.get(key)) for key in aum_expense):
            missing.append("aum_or_expense")
        else:
            present += 1
        if not any(_present(payload.get(key)) for key in allocation):
            missing.append("holdings_or_allocations")
        else:
            present += 1
    else:
        total += len(EQUITY_GROUPS)
        for group, keys in EQUITY_GROUPS.items():
            if any(_present(payload.get(key)) for key in keys):
                present += 1
            else:
                missing.append(group)
    return CandidateResult(
        provider, payload, not missing, round(present / total, 4), missing,
        str(payload.get("as_of") or "") or None,
    )


def evaluate_technicals(provider: str, payload: dict[str, Any]) -> CandidateResult:
    missing = [key for key in TECHNICAL_FIELDS if _number(payload.get(key)) is None]
    score = round((len(TECHNICAL_FIELDS) - len(missing)) / len(TECHNICAL_FIELDS), 4)
    return CandidateResult(
        provider, payload, not missing, score, missing,
        str(payload.get("as_of_bar") or "") or None,
    )


def evaluate_news(provider: str, headlines: Iterable[Any]) -> CandidateResult:
    unique: dict[str, Any] = {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    for item in headlines:
        key = str(getattr(item, "external_id", "") or getattr(item, "url", "") or "").strip()
        published = _datetime(getattr(item, "published_at", None))
        if key and getattr(item, "title", None) and (published is None or published >= cutoff):
            unique[key] = item
    payload = list(unique.values())
    missing = [] if payload else ["recent_linked_item"]
    dates = [str(getattr(item, "published_at", "") or "") for item in payload]
    return CandidateResult(
        provider, payload, bool(payload), 1.0 if payload else 0.0, missing,
        max(dates, default=None),
    )


def cache_wins(
    existing_score: float | None,
    existing_as_of: str | None,
    candidate: CandidateResult,
) -> bool:
    """Preserve cache when it is more complete, or equally complete and at least as fresh."""
    if existing_score is None:
        return False
    if float(existing_score) > candidate.score:
        return True
    if float(existing_score) < candidate.score:
        return False
    return _time_value(existing_as_of) >= _time_value(candidate.as_of)


def _present(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError):
        return None


def _value(value: Any, key: str) -> Any:
    try:
        return value[key]
    except (KeyError, TypeError):
        return getattr(value, key, None)


def _time_value(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
