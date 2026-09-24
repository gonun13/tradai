from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import requests

from adapters.base import AdapterMetadata, RatePolicy
from adapters.errors import AdapterPermanentError, AdapterRateLimitError, AdapterTransientError
from adapters.marketaux import NewsHeadline


class FinnhubNewsAdapter:
    name = "finnhub"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key.strip()
        self.last_response_headers: dict[str, str] = {}

    def enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            provider=self.name,
            regions=frozenset({"us"}),
            instrument_kinds=frozenset({"equity", "etf"}),
            operations=frozenset({"news"}),
            enabled=self.enabled(),
            rate_policy=RatePolicy(minimum_interval_seconds=1.05, window_seconds=60, window_limit=60),
        )

    def get_headlines(self, symbols: list[str], limit: int = 3) -> list[NewsHeadline]:
        if not self.enabled():
            raise RuntimeError("FINNHUB_API_KEY not set")
        symbol = symbols[0].split(".")[0].upper()
        today = datetime.now(timezone.utc).date()
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/company-news",
                params={
                    "symbol": symbol,
                    "from": (today - timedelta(days=14)).isoformat(),
                    "to": today.isoformat(),
                    "token": self.api_key,
                },
                timeout=45,
            )
        except requests.RequestException as exc:
            raise AdapterTransientError(f"Finnhub news request failed for {symbol}") from exc
        self.last_response_headers = dict(r.headers)
        if r.status_code == 429:
            retry = r.headers.get("Retry-After")
            try:
                seconds = float(retry) if retry else None
            except ValueError:
                seconds = None
            raise AdapterRateLimitError("Finnhub news rate limited", retry_after=seconds)
        if r.status_code >= 500:
            raise AdapterTransientError(f"Finnhub news HTTP {r.status_code} for {symbol}")
        if r.status_code >= 400:
            raise AdapterPermanentError(f"Finnhub news HTTP {r.status_code} for {symbol}")
        out: list[NewsHeadline] = []
        for article in (r.json() or [])[:limit]:
            published = article.get("datetime")
            out.append(NewsHeadline(
                external_id=str(article.get("id") or article.get("url") or ""),
                title=str(article.get("headline") or "Untitled"),
                snippet=str(article.get("summary") or "") or None,
                url=str(article.get("url") or "") or None,
                source_name=str(article.get("source") or "") or None,
                published_at=(
                    datetime.fromtimestamp(int(published), tz=timezone.utc).isoformat()
                    if published else None
                ),
                language="en",
                symbols=[symbol],
                raw_json=json.dumps(article),
            ))
        return [item for item in out if item.external_id]
