from __future__ import annotations

from datetime import datetime, timezone

import requests

from adapters import Bar, Quote
from adapters.base import AdapterMetadata, RatePolicy
from adapters.errors import AdapterRateLimitError, SymbolNotFoundError

_UA = "Mozilla/5.0 (compatible; Tradai/0.1; +local)"


class YFinanceHistoricalAdapter:
    """EU historical adapter.

    Prefers Yahoo's public chart JSON (lighter than the yfinance scraper),
    which avoids most rate-limit traps for a small personal book.
    """

    name = "yfinance"
    regions = {"eu", "us"}

    def __init__(self) -> None:
        self.last_response_headers: dict[str, str] = {}

    @property
    def metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            provider=self.name,
            regions=frozenset(self.regions),
            instrument_kinds=frozenset({"equity", "etf"}),
            operations=frozenset({"quote", "bars"}),
            enabled=True,
            rate_policy=RatePolicy(minimum_interval_seconds=0.25),
        )

    def get_quote(self, symbol: str, currency: str) -> Quote:
        meta = self._chart_meta(symbol, range_="5d")
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        if price is None or float(price) <= 0:
            raise RuntimeError(f"Yahoo chart returned no price for {symbol}")
        cur = (meta.get("currency") or currency or "EUR").upper()
        ts = meta.get("regularMarketTime")
        as_of = (
            datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            if ts
            else datetime.now(timezone.utc).isoformat()
        )
        return Quote(price=float(price), currency=cur, as_of=as_of, source=self.name)

    def get_bars(self, symbol: str, days: int = 120) -> list[Bar]:
        # ~6 months covers typical Stage 3 technical needs.
        range_ = "6mo" if days <= 140 else "1y"
        payload = self._chart(symbol, range_=range_, interval="1d")
        result = (payload.get("chart") or {}).get("result") or []
        if not result:
            return []
        node = result[0]
        timestamps = node.get("timestamp") or []
        quote = ((node.get("indicators") or {}).get("quote") or [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        bars: list[Bar] = []
        for i, ts in enumerate(timestamps):
            close = closes[i] if i < len(closes) else None
            if close is None:
                continue
            bars.append(
                Bar(
                    bar_date=datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d"),
                    open=self._f(opens[i] if i < len(opens) else None),
                    high=self._f(highs[i] if i < len(highs) else None),
                    low=self._f(lows[i] if i < len(lows) else None),
                    close=float(close),
                    volume=self._f(volumes[i] if i < len(volumes) else None),
                    source=self.name,
                )
            )
        return bars

    def _chart_meta(self, symbol: str, range_: str) -> dict:
        payload = self._chart(symbol, range_=range_, interval="1d")
        result = (payload.get("chart") or {}).get("result") or []
        if not result:
            err = (payload.get("chart") or {}).get("error")
            detail = None
            if isinstance(err, dict):
                detail = str(err.get("description") or err.get("code") or err)
            elif err:
                detail = str(err)
            raise SymbolNotFoundError(symbol, self.name, detail)
        return result[0].get("meta") or {}

    def _chart(self, symbol: str, range_: str, interval: str) -> dict:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        r = requests.get(
            url,
            params={"range": range_, "interval": interval},
            headers={"User-Agent": _UA},
            timeout=30,
        )
        self.last_response_headers = dict(r.headers)
        if r.status_code == 429:
            retry = r.headers.get("Retry-After")
            try:
                seconds = float(retry) if retry else None
            except ValueError:
                seconds = None
            raise AdapterRateLimitError("Yahoo chart rate limited", retry_after=seconds)
        if r.status_code == 404:
            raise SymbolNotFoundError(symbol, self.name, "HTTP 404")
        r.raise_for_status()
        payload = r.json()
        err = (payload.get("chart") or {}).get("error")
        if err and not ((payload.get("chart") or {}).get("result")):
            detail = None
            if isinstance(err, dict):
                detail = str(err.get("description") or err.get("code") or err)
            else:
                detail = str(err)
            raise SymbolNotFoundError(symbol, self.name, detail)
        return payload

    @staticmethod
    def _f(val: object) -> float | None:
        if val is None:
            return None
        try:
            f = float(val)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return None if f != f else f
