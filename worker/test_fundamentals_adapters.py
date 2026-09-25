from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from adapters.alpha_vantage import (
    AlphaVantageEquityFundamentalsAdapter,
    AlphaVantageEtfFundamentalsAdapter,
    alpha_vantage_symbol,
)
from adapters.fundamentals import FinnhubFundamentalsAdapter
from adapters.rate_state import PersistentRateLimiter
from refresh import MarketRefreshService


def response(status: int, payload=None, headers=None) -> Mock:
    result = Mock(status_code=status, headers=headers or {})
    if isinstance(payload, Exception):
        result.json.side_effect = payload
    else:
        result.json.return_value = payload
    return result


class FinnhubFundamentalsTests(unittest.TestCase):
    def test_profile_and_metrics_are_normalized(self):
        adapter = FinnhubFundamentalsAdapter("secret-finnhub")
        profile = {
            "name": "Example Inc", "currency": "USD", "exchange": "NASDAQ NMS",
            "country": "US", "finnhubIndustry": "Technology",
            "marketCapitalization": 125.5,
        }
        metrics = {"metric": {
            "peTTM": 20, "pbAnnual": "3.5", "netProfitMarginTTM": 12.5,
            "operatingMarginTTM": "None", "roeTTM": 18,
            "revenueGrowthTTMYoy": 7.25, "epsGrowthTTMYoy": -2,
            "totalDebt/totalEquityQuarterly": 40, "currentRatioQuarterly": 1.8,
        }}
        with patch(
            "adapters.fundamentals.requests.get",
            side_effect=[response(200, profile), response(200, metrics)],
        ) as get:
            result = adapter.get_fundamentals("aapl", "equity")

        self.assertEqual(2, get.call_count)
        self.assertEqual(125_500_000, result["market_cap"])
        self.assertEqual(0.125, result["profit_margin"])
        self.assertEqual(0.18, result["roe"])
        self.assertEqual(0.0725, result["revenue_growth"])
        self.assertEqual(-0.02, result["earnings_growth"])
        self.assertEqual(0.4, result["debt_to_equity"])
        self.assertIsNone(result["operating_margin"])
        self.assertNotIn("secret-finnhub", str(result))

    def test_failures_are_classified_and_credentials_are_redacted(self):
        cases = [
            (response(403, {}), "unsupported"),
            (response(429, {}, {"Retry-After": "5"}), "rate-limited"),
            (response(503, {}), "transient"),
            (response(200, ValueError("broken")), "permanent"),
            (response(200, {"error": "bad token"}), "permanent"),
        ]
        for mocked, kind in cases:
            with self.subTest(kind=kind):
                adapter = FinnhubFundamentalsAdapter("secret-finnhub-key")
                with patch("adapters.fundamentals.requests.get", return_value=mocked):
                    with self.assertRaises(Exception) as raised:
                        adapter.get_fundamentals("AAPL", "equity")
                self.assertEqual(kind, raised.exception.kind)
                self.assertNotIn("secret-finnhub-key", str(raised.exception))


class AlphaVantageFundamentalsTests(unittest.TestCase):
    def test_equity_combines_overview_with_latest_balance_sheet(self):
        overview = {
            "Symbol": "SAP.DEX", "Name": "SAP SE", "Currency": "EUR",
            "Exchange": "XETRA", "Country": "Germany", "Sector": "Technology",
            "Industry": "Software", "MarketCapitalization": "250000000000",
            "PERatio": "28.5", "ProfitMargin": "0.193", "OperatingMarginTTM": "0.21",
            "ReturnOnEquityTTM": "18%", "QuarterlyRevenueGrowthYOY": "0.09",
        }
        balance = {"quarterlyReports": [
            {
                "fiscalDateEnding": "2025-12-31", "totalCurrentAssets": "100",
                "totalCurrentLiabilities": "50", "totalLiabilities": "300",
                "totalShareholderEquity": "200", "cashAndCashEquivalentsAtCarryingValue": "20",
                "currentNetReceivables": "15",
            },
            {
                "fiscalDateEnding": "2026-03-31", "totalCurrentAssets": "120",
                "totalCurrentLiabilities": "40", "totalLiabilities": "320",
                "totalShareholderEquity": "160", "cashAndCashEquivalentsAtCarryingValue": "30",
                "currentNetReceivables": "10",
            },
        ]}
        adapter = AlphaVantageEquityFundamentalsAdapter("secret-alpha")
        with patch(
            "adapters.alpha_vantage.requests.get",
            side_effect=[response(200, overview), response(200, balance)],
        ) as get:
            result = adapter.get_fundamentals("SAP.DE", "equity", "XETR")

        self.assertEqual(2, get.call_count)
        self.assertEqual("SAP.DEX", get.call_args_list[0].kwargs["params"]["symbol"])
        self.assertEqual(0.18, result["roe"])
        self.assertEqual(3.0, result["current_ratio"])
        self.assertEqual(2.0, result["debt_to_equity"])
        self.assertEqual(1.0, result["quick_ratio"])
        self.assertEqual("2026-03-31", result["reported_at"])

    def test_etf_normalizes_assets_expense_holdings_and_allocations(self):
        payload = {
            "name": "Example ETF", "net_assets": "123456789",
            "net_expense_ratio": "0.25", "asset_class": "Equity",
            "holdings": [
                {"symbol": "AAA", "description": "A", "weight": "12.5"},
                {"symbol": "BBB", "description": "B", "weight": None},
            ],
            "sectors": [{"sector": "Technology", "weight": "30%"}],
            "asset_allocation": {"Stocks": "98.5", "Cash": "1.5"},
        }
        adapter = AlphaVantageEtfFundamentalsAdapter("secret-alpha")
        with patch(
            "adapters.alpha_vantage.requests.get", return_value=response(200, payload)
        ) as get:
            result = adapter.get_fundamentals("JEDI.DE", "etf", "XETR")

        self.assertEqual("JEDI.DEX", get.call_args.kwargs["params"]["symbol"])
        self.assertEqual(123456789, result["aum"])
        self.assertEqual(0.0025, result["expense_ratio"])
        self.assertEqual(0.125, result["holdings"][0]["weight"])
        self.assertIsNone(result["holdings"][1]["weight"])
        self.assertEqual(0.3, result["allocations"]["sectors"][0]["weight"])
        self.assertEqual(0.985, result["allocations"]["assets"]["Stocks"])

    def test_known_symbol_translation_and_unknown_passthrough(self):
        expected = {
            ("SAP.DE", None): "SAP.DEX",
            ("AIR.PA", None): "AIR.PAR",
            ("BMW.F", None): "BMW.FRA",
            ("ABC.DU", None): "ABC.DUS",
            ("SAP", "XETA"): "SAP.DEX",
            ("AIR", "XPAR"): "AIR.PAR",
            ("AAPL", "XNAS"): "AAPL",
            ("UNKNOWN.X", "XXXX"): "UNKNOWN.X",
        }
        for arguments, translated in expected.items():
            with self.subTest(arguments=arguments):
                self.assertEqual(translated, alpha_vantage_symbol(*arguments))

    def test_http_and_http_200_errors_are_classified_and_redacted(self):
        cases = [
            (response(403, {}), "permanent"),
            (response(429, {}, {"Retry-After": "5"}), "rate-limited"),
            (response(503, {}), "transient"),
            (response(200, {"Note": "25 requests per day"}), "quota-deferred"),
            (response(200, {"Information": "API rate limit reached"}), "quota-deferred"),
            (response(200, {"Error Message": "Invalid API call"}), "permanent"),
            (response(200, ValueError("broken")), "permanent"),
        ]
        for mocked, kind in cases:
            with self.subTest(kind=kind):
                adapter = AlphaVantageEtfFundamentalsAdapter("secret-alpha-key")
                with patch("adapters.alpha_vantage.requests.get", return_value=mocked):
                    with self.assertRaises(Exception) as raised:
                        adapter.get_fundamentals("QQQ", "etf")
                self.assertEqual(kind, raised.exception.kind)
                self.assertNotIn("secret-alpha-key", str(raised.exception))

    def test_equity_and_etf_quota_costs_persist_across_connections(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        os.environ["ALPHA_VANTAGE_RATE_WINDOW_LIMIT"] = "100"
        os.environ["ALPHA_VANTAGE_RATE_WINDOW_SECONDS"] = "60"
        try:
            service = MarketRefreshService(path)
            conn = service.connect()
            PersistentRateLimiter(conn).before_call(
                AlphaVantageEquityFundamentalsAdapter("key")
            )
            conn.close()
            conn = service.connect()
            PersistentRateLimiter(conn).before_call(
                AlphaVantageEtfFundamentalsAdapter("key")
            )
            row = conn.execute(
                "SELECT window_count FROM provider_rate_state WHERE provider='alpha-vantage'"
            ).fetchone()
            self.assertEqual(3, row["window_count"])
            for _ in range(11):
                PersistentRateLimiter(conn).before_call(
                    AlphaVantageEquityFundamentalsAdapter("key")
                )
            conn.close()
            conn = service.connect()
            with self.assertRaises(Exception) as raised:
                PersistentRateLimiter(conn).before_call(
                    AlphaVantageEtfFundamentalsAdapter("key")
                )
            self.assertEqual("quota-deferred", raised.exception.kind)
            self.assertEqual(
                25,
                conn.execute(
                    "SELECT window_count FROM provider_rate_state "
                    "WHERE provider='alpha-vantage'"
                ).fetchone()["window_count"],
            )
            conn.close()
        finally:
            os.environ.pop("ALPHA_VANTAGE_RATE_WINDOW_LIMIT", None)
            os.environ.pop("ALPHA_VANTAGE_RATE_WINDOW_SECONDS", None)
            os.unlink(path)

    def test_existing_cooldown_is_not_extended_when_a_skipped_call_is_deferred(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            service = MarketRefreshService(path)
            conn = service.connect()
            limiter = PersistentRateLimiter(conn)
            adapter = AlphaVantageEtfFundamentalsAdapter("key")
            limiter.before_call(adapter)
            limiter.defer(adapter.name, 60)
            original = conn.execute(
                "SELECT cooldown_until FROM provider_rate_state WHERE provider=?",
                (adapter.name,),
            ).fetchone()["cooldown_until"]

            with self.assertRaises(Exception) as raised:
                limiter.before_call(adapter)
            time.sleep(0.01)
            limiter.defer(adapter.name, raised.exception.retry_after)

            current = conn.execute(
                "SELECT cooldown_until FROM provider_rate_state WHERE provider=?",
                (adapter.name,),
            ).fetchone()["cooldown_until"]
            self.assertEqual(original, current)
            conn.close()
        finally:
            os.unlink(path)

    def test_registry_order_depends_on_region_and_kind(self):
        service = MarketRefreshService("/tmp/unused-routing-test.sqlite")
        self.assertEqual(
            ["finnhub", "alpha-vantage"],
            [adapter.name for adapter in service._fundamentals_registry("us", "equity")],
        )
        self.assertEqual(
            ["alpha-vantage", "yfinance"],
            [adapter.name for adapter in service._fundamentals_registry("eu", "equity")],
        )
        for region in ("us", "eu"):
            self.assertEqual(
                ["alpha-vantage", "yfinance"],
                [adapter.name for adapter in service._fundamentals_registry(region, "etf")],
            )


if __name__ == "__main__":
    unittest.main()
