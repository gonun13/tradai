from __future__ import annotations

import os
from dataclasses import asdict, dataclass

import requests

_UA = "Mozilla/5.0 (compatible; Tradai/0.1; +local)"

# 0019: the tracker needs a way in that isn't typing a ticker from memory. Yahoo carries the
# EU venue suffixes the book is full of (.PA / .DE / .AS); Finnhub is US-first but official
# and already keyed. Neither is load-bearing — each is tried independently and manual symbol
# entry stays open in the UI, so a dead vendor degrades the result instead of blocking an add.

# spec/domain.md invariant 3: listed equities and ETFs only. Anything else a vendor returns
# (currencies, indices, futures, mutual funds) is dropped rather than shown and rejected later.
_YAHOO_KINDS = {"EQUITY": "equity", "ETF": "etf"}
_FINNHUB_KINDS = {
    "Common Stock": "equity",
    "ADR": "equity",
    "GDR": "equity",
    "REIT": "equity",
    "ETP": "etf",
    "ETF": "etf",
}


@dataclass(frozen=True)
class SearchHit:
    symbol: str
    name: str | None
    exchange: str | None
    kind: str
    region: str
    currency: str | None
    source: str


def infer_region(symbol: str) -> str:
    """Same rule refresh.resolve_region applies — a venue suffix means a EU listing."""
    return "eu" if "." in symbol else "us"


class SymbolSearchAdapter:
    """Name-or-ticker lookup, merged across vendors and deduped on symbol."""

    name = "symbol_search"

    def __init__(self, finnhub_key: str | None = None) -> None:
        self.finnhub_key = (finnhub_key or os.environ.get("FINNHUB_API_KEY") or "").strip()

    def search(self, query: str, limit: int = 12) -> tuple[list[dict], list[str]]:
        """Returns (results, warnings). Warnings name a vendor that failed, not an error."""
        q = (query or "").strip()
        if not q:
            return [], []

        hits: list[SearchHit] = []
        warnings: list[str] = []

        for fetch, label in ((self._yahoo, "yahoo"), (self._finnhub, "finnhub")):
            try:
                hits.extend(fetch(q, limit))
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{label} search failed: {exc}")

        # Yahoo wins a collision: it carries the venue suffix, which is what the ingest
        # adapters actually need to resolve an EU listing.
        merged: dict[str, SearchHit] = {}
        for hit in hits:
            key = hit.symbol.upper()
            if key not in merged:
                merged[key] = hit

        ranked = sorted(merged.values(), key=lambda h: _rank(h, q))
        return [asdict(h) for h in ranked[:limit]], warnings

    def _yahoo(self, query: str, limit: int) -> list[SearchHit]:
        r = requests.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": query, "quotesCount": limit, "newsCount": 0, "listsCount": 0},
            headers={"User-Agent": _UA},
            timeout=15,
        )
        r.raise_for_status()
        out: list[SearchHit] = []
        for row in r.json().get("quotes") or []:
            symbol = str(row.get("symbol") or "").strip().upper()
            kind = _YAHOO_KINDS.get(str(row.get("quoteType") or "").upper())
            if not symbol or kind is None:
                continue
            out.append(
                SearchHit(
                    symbol=symbol,
                    name=row.get("longname") or row.get("shortname"),
                    exchange=row.get("exchDisp") or row.get("exchange"),
                    kind=kind,
                    region=infer_region(symbol),
                    # Yahoo's search payload carries no currency; it is resolved from the
                    # quote on add, where _chart_meta returns it for free.
                    currency=None,
                    source="yahoo",
                )
            )
        return out

    def _finnhub(self, query: str, limit: int) -> list[SearchHit]:
        if not self.finnhub_key:
            return []
        r = requests.get(
            "https://finnhub.io/api/v1/search",
            params={"q": query, "token": self.finnhub_key},
            timeout=15,
        )
        r.raise_for_status()
        out: list[SearchHit] = []
        for row in (r.json().get("result") or [])[: limit * 2]:
            symbol = str(row.get("symbol") or "").strip().upper()
            kind = _FINNHUB_KINDS.get(str(row.get("type") or "").strip())
            if not symbol or kind is None:
                continue
            out.append(
                SearchHit(
                    symbol=symbol,
                    name=row.get("description"),
                    exchange=None,
                    kind=kind,
                    region=infer_region(symbol),
                    currency=None,
                    source="finnhub",
                )
            )
        return out


def _rank(hit: SearchHit, query: str) -> tuple:
    """Exact ticker first, then prefix matches, then name matches — vendor order breaks ties."""
    q = query.strip().upper()
    symbol = hit.symbol.upper()
    base = symbol.split(".")[0]
    if symbol == q or base == q:
        tier = 0
    elif symbol.startswith(q):
        tier = 1
    elif q in (hit.name or "").upper():
        tier = 2
    else:
        tier = 3
    return (tier, 0 if hit.source == "yahoo" else 1, symbol)
