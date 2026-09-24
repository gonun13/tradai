from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests
from adapters.base import AdapterMetadata, RatePolicy
from adapters.errors import (
    AdapterPermanentError, AdapterQuotaDeferredError, AdapterRateLimitError,
    AdapterTransientError, AdapterUnsupportedError,
)


@dataclass
class NewsHeadline:
    external_id: str
    title: str
    snippet: str | None
    url: str | None
    source_name: str | None
    published_at: str | None
    language: str | None
    symbols: list[str]
    raw_json: str


_NAME_STOP = {
    "inc",
    "sa",
    "ag",
    "se",
    "plc",
    "corp",
    "corporation",
    "ltd",
    "limited",
    "etf",
    "ucits",
    "the",
    "and",
    "co",
    "company",
    "communications",
    "vectors",
    "shares",
    "trust",
    "fund",
    "index",
}

# Known EU UCITS → US sister tickers when Marketaux has no EU listing news.
# Checked before entity search (saves free-tier quota; works when search is capped).
_US_ETF_TWINS_BY_ISIN = {
    "IE0002PG6CA6": "REMX",  # VanEck Rare Earth and Strategic Metals UCITS (VVMX.DE)
}
_US_ETF_TWINS_BY_SYMBOL = {
    "VVMX.DE": "REMX",
    "VVMX": "REMX",
}
_WEAK_THEME = {
    "future",
    "energy",
    "global",
    "world",
    "market",
    "markets",
    "growth",
    "value",
    "income",
    "tech",
    "technology",
    "innovation",
    "innovators",
    "space",
    "clean",
    "green",
    "new",
    "next",
    "equity",
    "equities",
    "stock",
    "stocks",
    "bond",
    "bonds",
    "active",
    "smart",
}


class MarketauxNewsAdapter:
    """News adapter — US + EU via Marketaux (decision 0006).

    Free tier: 100 requests/day, 3 articles/request. Call per listing symbol
    (not one batched book call) so each holding can get its own headlines.
    EU UCITS ETFs often lack Marketaux coverage; fall back to a US sister ticker
    when entity search finds one (e.g. VVMX.DE → REMX).
    """

    name = "marketaux"
    regions = {"us", "eu"}
    base_url = "https://api.marketaux.com/v1/news/all"
    entity_url = "https://api.marketaux.com/v1/entity/search"

    def __init__(self, api_token: str) -> None:
        self.api_token = api_token.strip()
        self.last_response_headers: dict[str, str] = {}

    @property
    def metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            provider=self.name,
            regions=frozenset(self.regions),
            instrument_kinds=frozenset({"equity", "etf"}),
            operations=frozenset({"news"}),
            enabled=self.enabled(),
            rate_policy=RatePolicy(minimum_interval_seconds=0.75, window_seconds=86400, window_limit=100),
        )

    def enabled(self) -> bool:
        return bool(self.api_token)

    @staticmethod
    def query_symbol(symbol: str) -> str:
        """Marketaux entity keys are listing symbols (AM.PA, VXRT), not Yahoo-stripped bases."""
        return symbol.strip().upper()

    @staticmethod
    def looks_like_etf(kind: str | None, name: str | None) -> bool:
        if (kind or "").lower() == "etf":
            return True
        upper = (name or "").upper()
        return "ETF" in upper or "UCITS" in upper

    def get_headlines(self, symbols: list[str], limit: int = 3) -> list[NewsHeadline]:
        """Fetch up to `limit` headlines per symbol (one HTTP request each)."""
        if not self.enabled():
            raise RuntimeError("MARKETAUX_API_TOKEN not set")

        out: list[NewsHeadline] = []
        seen: set[str] = set()
        for raw in symbols:
            key = self.query_symbol(raw)
            if not key or key in seen:
                continue
            seen.add(key)
            out.extend(self._fetch_symbol(key, limit=limit))
        return out

    def resolve_alternate_symbol(self, symbol: str, name: str | None) -> str | None:
        """When the book ticker has no articles, try another listing of the same name."""
        return self._resolve_from_queries(
            symbol,
            name,
            queries=self._search_queries(name, etf_mode=False) if name else [],
            prefer_us_etf=False,
        )

    def resolve_us_etf_fallback(
        self,
        symbol: str,
        name: str | None,
        isin: str | None = None,
    ) -> str | None:
        """EU/UCITS ETF with no news → US sister ETF ticker when available."""
        primary = self.query_symbol(symbol)
        known = self._known_us_twin(primary, isin)
        if known and known != primary:
            return known
        if not name:
            return None
        return self._resolve_from_queries(
            symbol,
            name,
            queries=self._search_queries(name, etf_mode=True),
            prefer_us_etf=True,
        )

    @staticmethod
    def _known_us_twin(symbol: str, isin: str | None) -> str | None:
        if isin:
            hit = _US_ETF_TWINS_BY_ISIN.get(isin.strip().upper())
            if hit:
                return hit
        return _US_ETF_TWINS_BY_SYMBOL.get(symbol.upper())

    def _resolve_from_queries(
        self,
        symbol: str,
        name: str | None,
        *,
        queries: list[str],
        prefer_us_etf: bool,
    ) -> str | None:
        if not self.enabled() or not queries:
            return None

        primary = self.query_symbol(symbol)
        seen_queries: set[str] = set()
        candidates: list[dict] = []

        for q in queries:
            key = q.lower()
            if key in seen_queries:
                continue
            seen_queries.add(key)
            rows = self._entity_search(q)
            for ent in rows:
                sym = str(ent.get("symbol") or "").upper()
                ent_name = str(ent.get("name") or "")
                if not sym or sym == primary:
                    continue
                if name and not self._names_compatible(name, ent_name, etf_mode=prefer_us_etf):
                    continue
                if prefer_us_etf:
                    ent_type = str(ent.get("type") or "").lower()
                    country = str(ent.get("country") or "").lower()
                    # US sister only — never attach MX/HK/etc. listings as "US fallback".
                    if not self._is_us_listing(sym, country):
                        continue
                    if ent_type and ent_type not in ("etf", "equity"):
                        continue
                candidates.append(
                    {
                        "symbol": sym,
                        "name": ent_name,
                        "type": str(ent.get("type") or "").lower(),
                        "country": str(ent.get("country") or "").lower(),
                    }
                )
            # Stop early once we have a strong US bare ETF hit.
            if prefer_us_etf and any(
                "." not in c["symbol"]
                and c["type"] == "etf"
                and c["country"] in ("us", "")
                for c in candidates
            ):
                break

        if not candidates:
            return None

        if prefer_us_etf:
            candidates.sort(key=lambda c: self._us_etf_rank(c))
        else:
            candidates.sort(key=lambda c: self._listing_rank(c["symbol"]))

        return candidates[0]["symbol"]

    def _entity_search(self, search: str) -> list[dict]:
        try:
            r = requests.get(
                self.entity_url,
                params={
                    "api_token": self.api_token,
                    "search": search,
                },
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
        except Exception:  # noqa: BLE001
            return []
        rows = data.get("data") or []
        return [row for row in rows if isinstance(row, dict)]

    @classmethod
    def _search_queries(cls, name: str, *, etf_mode: bool) -> list[str]:
        cleaned = re.sub(r"[/|,;:()]+", " ", name)
        tokens = [
            t
            for t in "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in cleaned).split()
            if t.lower() not in _NAME_STOP
        ]
        if not tokens:
            return [name.strip()] if name.strip() else []

        queries: list[str] = []
        # Issuer + theme (best for VanEck Rare Earth → REMX).
        if len(tokens) >= 3:
            queries.append(" ".join(tokens[:4]))
            queries.append(" ".join(tokens[:3]))
        if len(tokens) >= 2:
            queries.append(" ".join(tokens[:2]))
        # Theme without issuer (Rare Earth Strategic Metals).
        if etf_mode and len(tokens) >= 3:
            queries.append(" ".join(tokens[1:4]))
            queries.append(" ".join(tokens[1:3]))
        queries.append(" ".join(tokens))
        # Dedupe preserving order.
        out: list[str] = []
        seen: set[str] = set()
        for q in queries:
            k = q.lower()
            if k not in seen:
                seen.add(k)
                out.append(q)
        return out

    @staticmethod
    def _is_us_listing(symbol: str, country: str) -> bool:
        if country == "us":
            return True
        if country and country != "us":
            return False
        # Bare tickers are usually US primary listings in Marketaux.
        return "." not in symbol

    @staticmethod
    def _us_etf_rank(ent: dict) -> tuple:
        sym = ent["symbol"]
        is_bare = "." not in sym
        is_us = ent["country"] in ("us", "")
        is_etf = ent["type"] == "etf"
        # Lower is better.
        return (
            0 if (is_bare and is_us and is_etf) else 1,
            0 if (is_bare and is_etf) else 1,
            0 if (is_bare and is_us) else 1,
            0 if is_etf else 1,
            0 if is_bare else 1,
            sym,
        )

    @staticmethod
    def _listing_rank(sym: str) -> tuple:
        preferred_suffixes = (".PA", ".DE", ".L", ".AS", ".BR", ".MI", ".MC")
        for i, suf in enumerate(preferred_suffixes):
            if sym.endswith(suf):
                return (i, sym)
        if "." not in sym:
            return (len(preferred_suffixes), sym)
        return (len(preferred_suffixes) + 1, sym)

    def _fetch_symbol(self, symbol: str, limit: int) -> list[NewsHeadline]:
        try:
            r = requests.get(
                self.base_url,
                params={
                    "api_token": self.api_token,
                    "symbols": symbol,
                    "filter_entities": "true",
                    "language": "en",
                    "limit": min(max(limit, 1), 3),
                    "published_after": (
                        datetime.now(timezone.utc) - timedelta(days=14)
                    ).strftime("%Y-%m-%dT%H:%M:%S"),
                },
                timeout=45,
            )
        except requests.RequestException as exc:
            raise AdapterTransientError(f"Marketaux request failed for {symbol}") from exc
        self.last_response_headers = dict(r.headers)
        if r.status_code == 429:
            retry = r.headers.get("Retry-After")
            try:
                seconds = float(retry) if retry else None
            except ValueError:
                seconds = None
            raise AdapterRateLimitError("Marketaux rate limited", retry_after=seconds)
        if r.status_code == 402:
            raise AdapterQuotaDeferredError("Marketaux usage limit exhausted")
        if r.status_code == 403:
            raise AdapterUnsupportedError("Marketaux news endpoint is not included in this plan")
        if r.status_code >= 500:
            raise AdapterTransientError(f"Marketaux HTTP {r.status_code}")
        if r.status_code >= 400:
            raise AdapterPermanentError(f"Marketaux HTTP {r.status_code}")
        data = r.json()
        out: list[NewsHeadline] = []
        for article in data.get("data") or []:
            uuid = str(article.get("uuid") or article.get("url") or "")
            if not uuid:
                continue
            hit_symbols: list[str] = []
            for ent in article.get("entities") or []:
                sym = ent.get("symbol")
                if sym:
                    hit_symbols.append(str(sym).upper())
            out.append(
                NewsHeadline(
                    external_id=uuid,
                    title=str(article.get("title") or "Untitled"),
                    snippet=(str(article["snippet"]) if article.get("snippet") else None),
                    url=(str(article["url"]) if article.get("url") else None),
                    source_name=(
                        str(article["source"])
                        if isinstance(article.get("source"), str)
                        else (
                            str(article["source"].get("name"))
                            if isinstance(article.get("source"), dict) and article["source"].get("name")
                            else None
                        )
                    ),
                    published_at=(
                        str(article["published_at"]) if article.get("published_at") else None
                    ),
                    language=(str(article["language"]) if article.get("language") else None),
                    symbols=hit_symbols or [symbol],
                    raw_json=json.dumps(article),
                )
            )
        return out

    @staticmethod
    def _names_compatible(book_name: str, entity_name: str, *, etf_mode: bool = False) -> bool:
        a = "".join(ch for ch in book_name.lower() if ch.isalnum() or ch.isspace()).split()
        b = "".join(
            ch if ch.isalnum() or ch.isspace() else " " for ch in entity_name.lower()
        ).split()
        if not a or not b:
            return False
        a_core = [t for t in a if t not in _NAME_STOP]
        b_core = [t for t in b if t not in _NAME_STOP]
        if not a_core:
            a_core = a
        if not b_core:
            b_core = b

        if etf_mode:
            # VanEck Rare Earth … ↔ VanEck Vectors Rare Earth … :
            # issuer match + theme, or ≥2 strong theme tokens (not just "Energy").
            shared = set(a_core) & set(b_core)
            strong = shared - _WEAK_THEME
            issuer_ok = a_core[0] == b_core[0] or a_core[0] in b_core or b_core[0] in a_core
            if issuer_ok and (len(strong) >= 1 or len(shared) >= 2):
                return True
            return len(strong) >= 2

        return a_core[0] == b_core[0] or a_core[0] in b_core or b_core[0] in a_core
