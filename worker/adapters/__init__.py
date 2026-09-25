from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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


class HistoricalAdapter(Protocol):
    name: str
    regions: set[str]

    def get_quote(self, symbol: str, currency: str) -> Quote: ...

    def get_bars(self, symbol: str, days: int = 120) -> list[Bar]: ...

    def get_long_history(self, symbol: str, currency: str) -> LongHistory: ...
