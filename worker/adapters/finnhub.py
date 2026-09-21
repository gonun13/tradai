from __future__ import annotations

from datetime import datetime, timezone

import requests

from adapters import Bar, Quote
from adapters.errors import SymbolNotFoundError


class FinnhubHistoricalAdapter:
    name = "finnhub"
    regions = {"us"}

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key.strip()

    def enabled(self) -> bool:
        return bool(self.api_key)

    def get_quote(self, symbol: str, currency: str) -> Quote:
        if not self.enabled():
            raise RuntimeError("FINNHUB_API_KEY not set")
        sym = symbol.split(".")[0].upper()
        r = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": sym, "token": self.api_key},
            timeout=30,
        )
        if r.status_code == 404:
            raise SymbolNotFoundError(symbol, self.name, f"HTTP 404 for {sym}")
        r.raise_for_status()
        data = r.json()
        price = data.get("c")
        if price is None or float(price) <= 0:
            raise SymbolNotFoundError(symbol, self.name, f"no price for {sym}")
        ts = data.get("t")
        as_of = (
            datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            if ts
            else datetime.now(timezone.utc).isoformat()
        )
        return Quote(price=float(price), currency=currency or "USD", as_of=as_of, source=self.name)

    def get_bars(self, symbol: str, days: int = 120) -> list[Bar]:
        if not self.enabled():
            raise RuntimeError("FINNHUB_API_KEY not set")
        import time

        sym = symbol.split(".")[0].upper()
        now = int(time.time())
        frm = now - days * 86400
        r = requests.get(
            "https://finnhub.io/api/v1/stock/candle",
            params={
                "symbol": sym,
                "resolution": "D",
                "from": frm,
                "to": now,
                "token": self.api_key,
            },
            timeout=45,
        )
        # Free Finnhub plans often block /stock/candle (403). Callers may fall back.
        if r.status_code == 403:
            raise RuntimeError(f"finnhub candles forbidden for {sym} (plan/tier)")
        if r.status_code == 404:
            raise SymbolNotFoundError(symbol, self.name, f"HTTP 404 candles for {sym}")
        try:
            r.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(f"finnhub candles HTTP {r.status_code} for {sym}") from exc
        data = r.json()
        if data.get("s") != "ok":
            return []
        bars: list[Bar] = []
        for i, ts in enumerate(data.get("t", [])):
            bars.append(
                Bar(
                    bar_date=datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d"),
                    open=float(data["o"][i]) if data.get("o") else None,
                    high=float(data["h"][i]) if data.get("h") else None,
                    low=float(data["l"][i]) if data.get("l") else None,
                    close=float(data["c"][i]),
                    volume=float(data["v"][i]) if data.get("v") else None,
                    source=self.name,
                )
            )
        return bars
