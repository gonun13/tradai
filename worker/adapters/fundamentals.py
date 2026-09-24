from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from adapters.base import AdapterMetadata, RatePolicy
from adapters.errors import (
    AdapterPermanentError, AdapterQuotaDeferredError, AdapterRateLimitError,
    AdapterTransientError, AdapterUnsupportedError, SymbolNotFoundError,
)

_UA = "Mozilla/5.0 (compatible; Tradai/0.2; +local)"


class FmpFundamentalsAdapter:
    name = "fmp"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key.strip()
        self.last_response_headers: dict[str, str] = {}
        self.base_url = "https://financialmodelingprep.com/stable"

    def enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            provider=self.name,
            regions=frozenset({"us", "eu"}),
            instrument_kinds=frozenset({"equity", "etf"}),
            operations=frozenset({"fundamentals"}),
            enabled=self.enabled(),
            rate_policy=RatePolicy(
                minimum_interval_seconds=0.3, window_seconds=86400,
                window_limit=250, request_cost=4,
            ),
        )

    def get_fundamentals(self, symbol: str, kind: str) -> dict[str, Any]:
        if not self.enabled():
            raise RuntimeError("FMP_API_KEY not set")
        sym = symbol.upper()
        if kind == "etf":
            info = self._one("etf/info", sym)
            holdings = self._many("etf/holdings", sym)
            sectors = self._many("etf/sector-weightings", sym)
            profile = self._one("profile", sym, optional=True)
            return {
                "symbol": sym,
                "name": _pick(info, "name", "fundName") or _pick(profile, "companyName"),
                "currency": _pick(info, "currency") or _pick(profile, "currency"),
                "exchange": _pick(info, "exchange", "exchangeShortName") or _pick(profile, "exchange"),
                "country": _pick(info, "country") or _pick(profile, "country"),
                "category": _pick(info, "category"),
                "aum": _pick(info, "assetsUnderManagement", "aum"),
                "expense_ratio": _pick(info, "expenseRatio", "expenseRatioPercentage"),
                "holdings": holdings[:25],
                "allocations": sectors,
                "as_of": datetime.now(timezone.utc).isoformat(),
            }

        profile = self._one("profile", sym)
        ratios = self._one("ratios-ttm", sym, optional=True)
        metrics = self._one("key-metrics-ttm", sym, optional=True)
        return {
            "symbol": sym,
            "name": _pick(profile, "companyName"),
            "currency": _pick(profile, "currency"),
            "exchange": _pick(profile, "exchangeShortName", "exchange"),
            "country": _pick(profile, "country"),
            "sector": _pick(profile, "sector"),
            "industry": _pick(profile, "industry"),
            "description": _pick(profile, "description"),
            "market_cap": _pick(profile, "marketCap", "mktCap"),
            "pe_ratio": _pick(ratios, "priceToEarningsRatioTTM", "priceEarningsRatioTTM"),
            "price_to_book": _pick(ratios, "priceToBookRatioTTM"),
            "enterprise_value": _pick(metrics, "enterpriseValueTTM"),
            "profit_margin": _pick(ratios, "netProfitMarginTTM"),
            "operating_margin": _pick(ratios, "operatingProfitMarginTTM"),
            "roe": _pick(ratios, "returnOnEquityTTM"),
            "roa": _pick(ratios, "returnOnAssetsTTM"),
            "revenue_growth": _pick(ratios, "revenueGrowthTTM"),
            "earnings_growth": _pick(ratios, "netIncomeGrowthTTM"),
            "debt_to_equity": _pick(ratios, "debtEquityRatioTTM", "debtToEquityRatioTTM"),
            "current_ratio": _pick(ratios, "currentRatioTTM"),
            "quick_ratio": _pick(ratios, "quickRatioTTM"),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    def _many(self, endpoint: str, symbol: str, *, optional: bool = False) -> list[dict[str, Any]]:
        try:
            r = requests.get(
                f"{self.base_url}/{endpoint}",
                params={"symbol": symbol, "apikey": self.api_key},
                timeout=45,
            )
        except requests.RequestException as exc:
            raise AdapterTransientError(f"FMP {endpoint} request failed for {symbol}") from exc
        self.last_response_headers = dict(r.headers)
        if r.status_code == 429:
            raise AdapterRateLimitError(
                f"FMP {endpoint} rate limited", retry_after=_retry_after(r.headers.get("Retry-After"))
            )
        if r.status_code == 402:
            raise AdapterQuotaDeferredError(f"FMP {endpoint} plan quota exhausted")
        if r.status_code == 403:
            if optional:
                return []
            raise AdapterUnsupportedError(f"FMP {endpoint} is not included in this plan")
        if optional and r.status_code in (402, 403, 404):
            return []
        if r.status_code == 404:
            raise SymbolNotFoundError(symbol, self.name, endpoint)
        if r.status_code >= 500:
            raise AdapterTransientError(f"FMP {endpoint} HTTP {r.status_code}")
        if r.status_code >= 400:
            raise AdapterPermanentError(f"FMP {endpoint} HTTP {r.status_code}")
        data = r.json()
        if isinstance(data, dict):
            rows = data.get("data") or data.get("results") or []
        else:
            rows = data
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def _one(self, endpoint: str, symbol: str, *, optional: bool = False) -> dict[str, Any]:
        rows = self._many(endpoint, symbol, optional=optional)
        if not rows and not optional:
            raise SymbolNotFoundError(symbol, self.name, endpoint)
        return rows[0] if rows else {}


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

    def get_fundamentals(self, symbol: str, kind: str) -> dict[str, Any]:
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


def _retry_after(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None
