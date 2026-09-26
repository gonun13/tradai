from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests
from domain.providers.base import AdapterMetadata, RatePolicy
from domain.providers.errors import (
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


class MarketauxNewsAdapter:
    """News adapter — US + EU via Marketaux (decision 0006).

    Free tier: 100 requests/day, 3 articles/request. Call per listing symbol
    (not one batched book call) so each holding can get its own headlines.
    """

    name = "marketaux"
    regions = {"us", "eu"}
    base_url = "https://api.marketaux.com/v1/news/all"

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
