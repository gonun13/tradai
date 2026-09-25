from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from adapters.base import AdapterMetadata, RatePolicy
from adapters.errors import (
    AdapterPermanentError, AdapterRateLimitError, AdapterTransientError,
    AdapterUnsupportedError, SymbolNotFoundError,
)

_UA = "Mozilla/5.0 (compatible; Tradai/0.2; +local)"


class FinnhubFundamentalsAdapter:
    """US equity profile and basic metrics normalized as one Finnhub snapshot."""

    name = "finnhub"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key.strip()
        self.last_response_headers: dict[str, str] = {}
        self.base_url = "https://finnhub.io/api/v1"

    def enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            provider=self.name,
            regions=frozenset({"us"}),
            instrument_kinds=frozenset({"equity"}),
            operations=frozenset({"fundamentals"}),
            enabled=self.enabled(),
            rate_policy=RatePolicy(
                minimum_interval_seconds=1.05, window_seconds=60,
                window_limit=60, request_cost=2,
            ),
        )

    def get_fundamentals(
        self, symbol: str, kind: str, mic: str | None = None
    ) -> dict[str, Any]:
        if not self.enabled():
            raise RuntimeError("FINNHUB_API_KEY not set")
        sym = symbol.upper()
        profile = self._get("stock/profile2", sym)
        metrics = self._get("stock/metric", sym, metric="all")
        metric = metrics.get("metric") if isinstance(metrics.get("metric"), dict) else {}
        if not profile:
            raise SymbolNotFoundError(symbol, self.name, "empty company profile")
        return {
            "symbol": sym,
            "name": _pick(profile, "name"),
            "currency": _pick(profile, "currency"),
            "exchange": _pick(profile, "exchange"),
            "country": _pick(profile, "country"),
            "industry": _pick(profile, "finnhubIndustry"),
            # Finnhub documents marketCapitalization in millions of the profile currency.
            "market_cap": _scaled(_pick(profile, "marketCapitalization"), 1_000_000),
            "pe_ratio": _number(_pick(metric, "peTTM", "peBasicExclExtraTTM")),
            "price_to_book": _number(_pick(metric, "pbAnnual", "pbQuarterly")),
            "enterprise_value": _scaled(
                _pick(metric, "enterpriseValue", "enterpriseValueAnnual"), 1_000_000
            ),
            "profit_margin": _percentage(_pick(metric, "netProfitMarginTTM")),
            "operating_margin": _percentage(_pick(metric, "operatingMarginTTM")),
            "roe": _percentage(_pick(metric, "roeTTM", "roeRfy")),
            "roa": _percentage(_pick(metric, "roaTTM", "roaRfy")),
            "revenue_growth": _percentage(_pick(
                metric, "revenueGrowthTTMYoy", "revenueGrowthQuarterlyYoy"
            )),
            "earnings_growth": _percentage(_pick(
                metric, "epsGrowthTTMYoy", "epsGrowthQuarterlyYoy"
            )),
            "debt_to_equity": _percentage(_pick(
                metric, "totalDebt/totalEquityQuarterly", "totalDebt/totalEquityAnnual"
            )),
            "current_ratio": _number(_pick(metric, "currentRatioQuarterly", "currentRatioAnnual")),
            "quick_ratio": _number(_pick(metric, "quickRatioQuarterly", "quickRatioAnnual")),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    def _get(self, endpoint: str, symbol: str, **params: str) -> dict[str, Any]:
        try:
            r = requests.get(
                f"{self.base_url}/{endpoint}",
                params={"symbol": symbol, "token": self.api_key, **params},
                timeout=45,
            )
        except requests.RequestException as exc:
            raise AdapterTransientError(f"Finnhub {endpoint} request failed for {symbol}") from exc
        self.last_response_headers = dict(r.headers)
        if r.status_code == 429:
            raise AdapterRateLimitError(
                f"Finnhub {endpoint} rate limited",
                retry_after=_retry_after(r.headers.get("Retry-After")),
            )
        if r.status_code == 403:
            raise AdapterUnsupportedError(f"Finnhub {endpoint} is not included in this plan")
        if r.status_code == 404:
            raise SymbolNotFoundError(symbol, self.name, endpoint)
        if r.status_code >= 500:
            raise AdapterTransientError(f"Finnhub {endpoint} HTTP {r.status_code}")
        if r.status_code >= 400:
            raise AdapterPermanentError(f"Finnhub {endpoint} HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError as exc:
            raise AdapterPermanentError(f"Finnhub {endpoint} returned malformed JSON") from exc
        if not isinstance(data, dict):
            raise AdapterPermanentError(f"Finnhub {endpoint} returned an invalid payload")
        if data.get("error"):
            raise AdapterPermanentError(f"Finnhub {endpoint} returned an error")
        return data


class YFinanceFundamentalsAdapter:
    """Light fundamentals from Yahoo quote-summary; independent from bar retrieval."""

    name = "yfinance"
    modules = (
        "assetProfile,summaryDetail,defaultKeyStatistics,financialData,price,"
        "fundProfile,topHoldings"
    )

    def __init__(self) -> None:
        self.last_response_headers: dict[str, str] = {}

    def enabled(self) -> bool:
        return True

    @property
    def metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            provider=self.name,
            regions=frozenset({"us", "eu"}),
            instrument_kinds=frozenset({"equity", "etf"}),
            operations=frozenset({"fundamentals"}),
            enabled=True,
            rate_policy=RatePolicy(minimum_interval_seconds=0.25),
        )

    def get_fundamentals(
        self, symbol: str, kind: str, mic: str | None = None
    ) -> dict[str, Any]:
        r = requests.get(
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
            params={"modules": self.modules},
            headers={"User-Agent": _UA},
            timeout=45,
        )
        self.last_response_headers = dict(r.headers)
        if r.status_code == 429:
            raise AdapterRateLimitError(
                "Yahoo fundamentals rate limited", retry_after=_retry_after(r.headers.get("Retry-After"))
            )
        if r.status_code in (401, 403):
            return self._chart_fallback(symbol)
        if r.status_code == 404:
            raise SymbolNotFoundError(symbol, self.name, "quoteSummary")
        r.raise_for_status()
        result = ((r.json().get("quoteSummary") or {}).get("result") or [])
        if not result:
            return self._chart_fallback(symbol)
        node = result[0]
        profile = node.get("assetProfile") or {}
        summary = node.get("summaryDetail") or {}
        stats = node.get("defaultKeyStatistics") or {}
        financial = node.get("financialData") or {}
        price = node.get("price") or {}
        fund = node.get("fundProfile") or {}
        top = node.get("topHoldings") or {}
        common = {
            "symbol": symbol.upper(),
            "name": _raw(price.get("longName")) or _raw(price.get("shortName")),
            "currency": _raw(price.get("currency")),
            "exchange": _raw(price.get("exchangeName")) or _raw(price.get("exchange")),
            "country": profile.get("country"),
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
            "description": profile.get("longBusinessSummary"),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
        if kind == "etf":
            common.update({
                "category": _raw(fund.get("categoryName")),
                "aum": _raw(summary.get("totalAssets")),
                "expense_ratio": _raw(fund.get("feesExpensesInvestment")),
                "holdings": top.get("holdings") or [],
                "allocations": top.get("sectorWeightings") or top.get("equityHoldings") or [],
            })
        else:
            common.update({
                "market_cap": _raw(price.get("marketCap")) or _raw(summary.get("marketCap")),
                "pe_ratio": _raw(summary.get("trailingPE")),
                "price_to_book": _raw(stats.get("priceToBook")),
                "enterprise_value": _raw(stats.get("enterpriseValue")),
                "profit_margin": _raw(financial.get("profitMargins")),
                "operating_margin": _raw(financial.get("operatingMargins")),
                "roe": _raw(financial.get("returnOnEquity")),
                "roa": _raw(financial.get("returnOnAssets")),
                "revenue_growth": _raw(financial.get("revenueGrowth")),
                "earnings_growth": _raw(financial.get("earningsGrowth")),
                "debt_to_equity": _raw(financial.get("debtToEquity")),
                "current_ratio": _raw(financial.get("currentRatio")),
                "quick_ratio": _raw(financial.get("quickRatio")),
            })
        return common

    def _chart_fallback(self, symbol: str) -> dict[str, Any]:
        """Yahoo often gates quoteSummary; chart metadata is the useful one-source partial."""
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "5d", "interval": "1d"},
            headers={"User-Agent": _UA},
            timeout=30,
        )
        self.last_response_headers = dict(r.headers)
        if r.status_code == 429:
            raise AdapterRateLimitError(
                "Yahoo fundamentals chart fallback rate limited",
                retry_after=_retry_after(r.headers.get("Retry-After")),
            )
        if r.status_code == 404:
            raise SymbolNotFoundError(symbol, self.name, "chart metadata")
        r.raise_for_status()
        result = ((r.json().get("chart") or {}).get("result") or [])
        if not result:
            raise SymbolNotFoundError(symbol, self.name, "empty chart metadata")
        meta = result[0].get("meta") or {}
        return {
            "symbol": str(meta.get("symbol") or symbol).upper(),
            "name": meta.get("longName") or meta.get("shortName") or symbol.upper(),
            "currency": meta.get("currency"),
            "exchange": meta.get("exchangeName") or meta.get("fullExchangeName"),
            "instrument_type": meta.get("instrumentType"),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }


def _raw(value: Any) -> Any:
    return value.get("raw") if isinstance(value, dict) and "raw" in value else value


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _number(value: Any) -> float | None:
    if value is None or value == "" or str(value).strip().lower() in {"none", "null", "-"}:
        return None
    try:
        result = float(str(value).replace(",", ""))
        return result if result == result else None
    except (TypeError, ValueError):
        return None


def _scaled(value: Any, factor: float) -> float | None:
    number = _number(value)
    return number * factor if number is not None else None


def _percentage(value: Any) -> float | None:
    number = _number(str(value).rstrip("%") if value is not None else None)
    return number / 100 if number is not None else None


def _retry_after(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None
