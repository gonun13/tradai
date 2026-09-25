from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from adapters.base import AdapterMetadata, RatePolicy
from adapters.errors import (
    AdapterPermanentError,
    AdapterQuotaDeferredError,
    AdapterRateLimitError,
    AdapterTransientError,
    AdapterUnsupportedError,
    SymbolNotFoundError,
)


class _AlphaVantageFundamentalsAdapter:
    name = "alpha-vantage"
    instrument_kind = "equity"
    request_cost = 1

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key.strip()
        self.last_response_headers: dict[str, str] = {}
        self.base_url = "https://www.alphavantage.co/query"

    def enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            provider=self.name,
            regions=frozenset({"us", "eu"}),
            instrument_kinds=frozenset({self.instrument_kind}),
            operations=frozenset({"fundamentals"}),
            enabled=self.enabled(),
            rate_policy=RatePolicy(
                minimum_interval_seconds=0.0,
                window_seconds=86400,
                window_limit=25,
                request_cost=self.request_cost,
                minimum_window_seconds=86400,
                maximum_window_limit=25,
            ),
        )

    def _request(self, function: str, symbol: str) -> dict[str, Any]:
        try:
            response = requests.get(
                self.base_url,
                params={"function": function, "symbol": symbol, "apikey": self.api_key},
                timeout=45,
            )
        except requests.RequestException as exc:
            raise AdapterTransientError(
                f"Alpha Vantage {function} request failed for {symbol}"
            ) from exc
        self.last_response_headers = dict(response.headers)
        if response.status_code == 429:
            raise AdapterRateLimitError(
                f"Alpha Vantage {function} rate limited",
                retry_after=_retry_after(response.headers.get("Retry-After")),
            )
        if response.status_code == 403:
            raise AdapterPermanentError(f"Alpha Vantage {function} HTTP 403")
        if response.status_code == 404:
            raise SymbolNotFoundError(symbol, self.name, function)
        if response.status_code >= 500:
            raise AdapterTransientError(
                f"Alpha Vantage {function} HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise AdapterPermanentError(
                f"Alpha Vantage {function} HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise AdapterPermanentError(
                f"Alpha Vantage {function} returned malformed JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise AdapterPermanentError(
                f"Alpha Vantage {function} returned an invalid payload"
            )
        self._raise_payload_error(function, payload)
        return payload

    @staticmethod
    def _raise_payload_error(function: str, payload: dict[str, Any]) -> None:
        note = payload.get("Note")
        information = payload.get("Information")
        error = payload.get("Error Message") or payload.get("Error")
        if note:
            raise AdapterQuotaDeferredError(
                f"Alpha Vantage {function} quota deferred", retry_after=86400
            )
        if information:
            detail = str(information).lower()
            if any(word in detail for word in ("limit", "rate", "frequency", "calls")):
                raise AdapterQuotaDeferredError(
                    f"Alpha Vantage {function} quota deferred", retry_after=86400
                )
            raise AdapterUnsupportedError(f"Alpha Vantage {function} unavailable")
        if error:
            raise AdapterPermanentError(f"Alpha Vantage {function} returned an error")


class AlphaVantageEquityFundamentalsAdapter(_AlphaVantageFundamentalsAdapter):
    instrument_kind = "equity"
    request_cost = 2

    def get_fundamentals(
        self, symbol: str, kind: str, mic: str | None = None
    ) -> dict[str, Any]:
        query_symbol = alpha_vantage_symbol(symbol, mic)
        overview = self._request("OVERVIEW", query_symbol)
        balance_sheet = self._request("BALANCE_SHEET", query_symbol)
        if not overview:
            raise SymbolNotFoundError(symbol, self.name, "empty OVERVIEW")
        report = _latest_report(balance_sheet)
        current_assets = _number(_pick(report, "totalCurrentAssets"))
        current_liabilities = _number(_pick(report, "totalCurrentLiabilities"))
        cash = _number(_pick(
            report, "cashAndCashEquivalentsAtCarryingValue", "cashAndShortTermInvestments"
        ))
        receivables = _number(_pick(report, "currentNetReceivables", "netReceivables"))
        liabilities = _number(_pick(report, "totalLiabilities"))
        equity = _number(_pick(
            report, "totalShareholderEquity", "totalStockholdersEquity"
        ))
        return {
            "symbol": str(overview.get("Symbol") or query_symbol).upper(),
            "name": _clean(overview.get("Name")),
            "currency": _clean(overview.get("Currency")),
            "exchange": _clean(overview.get("Exchange")),
            "country": _clean(overview.get("Country")),
            "sector": _clean(overview.get("Sector")),
            "industry": _clean(overview.get("Industry")),
            "description": _clean(overview.get("Description")),
            "market_cap": _number(overview.get("MarketCapitalization")),
            "pe_ratio": _number(overview.get("PERatio")),
            "price_to_book": _number(overview.get("PriceToBookRatio")),
            "enterprise_value": _number(overview.get("EnterpriseValue")),
            "profit_margin": _decimal_fraction(overview.get("ProfitMargin")),
            "operating_margin": _decimal_fraction(overview.get("OperatingMarginTTM")),
            "roe": _decimal_fraction(overview.get("ReturnOnEquityTTM")),
            "roa": _decimal_fraction(overview.get("ReturnOnAssetsTTM")),
            "revenue_growth": _decimal_fraction(overview.get("QuarterlyRevenueGrowthYOY")),
            "earnings_growth": _decimal_fraction(overview.get("QuarterlyEarningsGrowthYOY")),
            "debt_to_equity": _ratio(liabilities, equity),
            "current_ratio": _ratio(current_assets, current_liabilities),
            "quick_ratio": _ratio(
                _sum_optional(cash, receivables), current_liabilities
            ),
            "reported_at": _pick(report, "fiscalDateEnding") or overview.get("LatestQuarter"),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }


class AlphaVantageEtfFundamentalsAdapter(_AlphaVantageFundamentalsAdapter):
    instrument_kind = "etf"
    request_cost = 1

    def get_fundamentals(
        self, symbol: str, kind: str, mic: str | None = None
    ) -> dict[str, Any]:
        query_symbol = alpha_vantage_symbol(symbol, mic)
        profile = self._request("ETF_PROFILE", query_symbol)
        if not profile:
            raise SymbolNotFoundError(symbol, self.name, "empty ETF_PROFILE")
        holdings = [
            {
                **row,
                "weight": _percentage_points(row.get("weight")),
            }
            for row in (profile.get("holdings") or [])[:25]
            if isinstance(row, dict)
        ]
        sectors = [
            {
                **row,
                "weight": _percentage_points(row.get("weight")),
            }
            for row in (profile.get("sectors") or [])
            if isinstance(row, dict)
        ]
        asset_allocation = profile.get("asset_allocation") or {}
        normalized_assets = {
            key: _percentage_points(value)
            for key, value in asset_allocation.items()
        } if isinstance(asset_allocation, dict) else {}
        allocations: dict[str, Any] = {}
        if sectors:
            allocations["sectors"] = sectors
        if normalized_assets:
            allocations["assets"] = normalized_assets
        return {
            "symbol": query_symbol,
            "name": _pick(profile, "name", "fund_name") or query_symbol,
            "description": _clean(profile.get("description")),
            "category": _pick(profile, "asset_class", "fund_family"),
            "aum": _number(_pick(profile, "net_assets", "assets_under_management")),
            "expense_ratio": _percentage_points(_pick(
                profile, "net_expense_ratio", "expense_ratio"
            )),
            "holdings": holdings,
            "allocations": allocations,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }


def alpha_vantage_symbol(symbol: str, mic: str | None = None) -> str:
    """Translate known European venue suffixes without spending search quota."""
    value = symbol.strip().upper()
    suffixes = {".DE": ".DEX", ".F": ".FRA", ".PA": ".PAR", ".DU": ".DUS"}
    for source, target in suffixes.items():
        if value.endswith(source):
            return value[:-len(source)] + target
    mic_suffixes = {
        "XETR": ".DEX", "XETA": ".DEX", "XFRA": ".FRA",
        "XPAR": ".PAR", "XDUS": ".DUS",
    }
    suffix = mic_suffixes.get(str(mic or "").strip().upper())
    return value + suffix if suffix and "." not in value else value


def _latest_report(payload: dict[str, Any]) -> dict[str, Any]:
    reports = payload.get("quarterlyReports") or payload.get("annualReports") or []
    valid = [row for row in reports if isinstance(row, dict)] if isinstance(reports, list) else []
    return max(valid, key=lambda row: str(row.get("fiscalDateEnding") or ""), default={})


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if _clean(value) is not None:
            return value
    return None


def _clean(value: Any) -> Any:
    if value is None or str(value).strip().lower() in {"", "none", "null", "-"}:
        return None
    return value


def _number(value: Any) -> float | None:
    value = _clean(value)
    if value is None:
        return None
    try:
        number = float(str(value).replace(",", "").rstrip("%"))
        return number if number == number else None
    except (TypeError, ValueError):
        return None


def _decimal_fraction(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    return number / 100 if str(value).strip().endswith("%") else number


def _percentage_points(value: Any) -> float | None:
    number = _number(value)
    return number / 100 if number is not None else None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _sum_optional(*values: float | None) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _retry_after(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None
