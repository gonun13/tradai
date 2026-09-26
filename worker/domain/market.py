from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Quote:
    price: float
    currency: str
    as_of: str
    source: str


@dataclass
class Bar:
    bar_date: str
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None
    source: str


@dataclass
class HistoricalPoint:
    point_date: str
    adjusted_close: float
    resolution: str = "daily"


@dataclass
class LongHistory:
    points: list[HistoricalPoint]
    currency: str
    as_of: str | None
