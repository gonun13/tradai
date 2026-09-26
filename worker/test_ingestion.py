from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from domain.market import Bar, HistoricalPoint, LongHistory, Quote
from domain.providers.base import AdapterMetadata, RatePolicy, select_first_complete
from domain.providers.errors import AdapterTransientError
from infrastructure.adapters.marketaux import MarketauxNewsAdapter, NewsHeadline
from infrastructure.adapters.rate_state import PersistentRateLimiter
from domain.ingestion import (
    cache_wins,
    compact_long_history,
    evaluate_bars,
    evaluate_fundamentals,
    evaluate_long_history,
    evaluate_news,
    evaluate_quote,
    evaluate_technicals,
    long_history_cache_wins,
)
from services.refresh import MarketRefreshService


class FakeAdapter:
    def __init__(self, provider: str, *, enabled: bool = True, quote=None, bars=None,
                 long_history=None, fundamentals=None, news=None, regions=None, kinds=None) -> None:
        self.provider = provider
        self.is_enabled = enabled
        self.quote = quote
        self.bars = bars or []
        self.long_history = long_history
        self.fundamentals = fundamentals or {}
        self.news = news or []
        self.calls = {"quote": 0, "bars": 0, "long_history": 0, "fundamentals": 0, "news": 0}
        self.last_response_headers = {}
        self.regions = frozenset(regions or {"us", "eu"})
        self.kinds = frozenset(kinds or {"equity", "etf"})

    @staticmethod
    def _resolve(value, symbol):
        return value(symbol) if callable(value) else value

    @property
    def metadata(self):
        return AdapterMetadata(
            self.provider, self.regions, self.kinds,
            frozenset(self.calls), self.is_enabled, RatePolicy(),
        )

    def get_quote(self, symbol, currency):
        self.calls["quote"] += 1
        value = self._resolve(self.quote, symbol)
        if isinstance(value, Exception):
            raise value
        return value

    def get_bars(self, symbol, days=370):
        self.calls["bars"] += 1
        value = self._resolve(self.bars, symbol)
        if isinstance(value, Exception):
            raise value
        return value

    def get_long_history(self, symbol, currency):
        self.calls["long_history"] += 1
        value = self._resolve(self.long_history, symbol)
        if isinstance(value, Exception):
            raise value
        if value is not None:
            return value
        bars = self._resolve(self.bars, symbol)
        return LongHistory(
            [HistoricalPoint(bar.bar_date, bar.close) for bar in bars], currency,
            bars[-1].bar_date if bars else None,
        )

    def get_fundamentals(self, symbol, kind, mic=None):
        self.calls["fundamentals"] += 1
        value = self._resolve(self.fundamentals, symbol)
        if isinstance(value, Exception):
            raise value
        return value

    def get_headlines(self, symbols, limit=3):
        self.calls["news"] += 1
        value = self._resolve(self.news, symbols[0])
        if isinstance(value, Exception):
            raise value
        return value


class ContractTests(unittest.TestCase):
    def test_long_history_validation_compaction_and_cache_protection(self):
        latest = datetime(2026, 9, 25, tzinfo=timezone.utc).date()
        points = []
        for offset in range(0, 8 * 366):
            day = latest - timedelta(days=offset)
            if day.weekday() < 5:
                points.append(HistoricalPoint(day.isoformat(), 100 + offset / 10))
        points.extend([
            HistoricalPoint(latest.isoformat(), 123.0),  # last duplicate wins
            HistoricalPoint("bad-date", 1),
            HistoricalPoint("2020-01-01", float("nan")),
            HistoricalPoint("2020-01-02", -1),
        ])
        compacted = compact_long_history(points)
        self.assertEqual(latest.isoformat(), compacted[-1].point_date)
        self.assertEqual(123.0, compacted[-1].adjusted_close)
        recent = [p for p in compacted if p.point_date >= "2025-09-25"]
        middle = [p for p in compacted if "2021-09-25" <= p.point_date < "2025-09-25"]
        old = [p for p in compacted if p.point_date < "2021-09-25"]
        self.assertTrue(recent and all(p.resolution == "daily" for p in recent))
        self.assertTrue(middle and all(p.resolution == "weekly" for p in middle))
        self.assertTrue(old and all(p.resolution == "monthly" for p in old))
        self.assertEqual(len(compacted), len({p.point_date for p in compacted}))

        candidate = evaluate_long_history("yfinance", LongHistory(points, "USD", None))
        self.assertTrue(candidate.complete)
        self.assertFalse(long_history_cache_wins("2019-01-01", "2026-09-24", candidate))
        self.assertTrue(long_history_cache_wins("2010-01-01", "2026-09-24", candidate))
        incomplete = evaluate_long_history(
            "yfinance", LongHistory([HistoricalPoint("2026-09-25", 1)], "USD", None)
        )
        self.assertFalse(incomplete.complete)
        self.assertTrue(long_history_cache_wins("2020-01-01", "2026-09-24", incomplete))

    def test_completeness_contracts(self):
        now = datetime.now(timezone.utc).isoformat()
        self.assertTrue(evaluate_quote("a", Quote(10, "USD", now, "a")).complete)
        self.assertFalse(evaluate_quote("a", Quote(0, "", "", "a")).complete)

        complete_bars = [
            Bar(f"2026-01-{(i % 28) + 1:02d}-{i:03d}", 1, 2, .5, float(i + 1), 10, "a")
            for i in range(127)
        ]
        self.assertTrue(evaluate_bars("a", complete_bars).complete)
        self.assertFalse(evaluate_bars("a", complete_bars[:126]).complete)

        equity = {
            "name": "A", "symbol": "A", "market_cap": 1, "profit_margin": .1,
            "revenue_growth": .1, "current_ratio": 2, "as_of": now,
        }
        self.assertTrue(evaluate_fundamentals("a", equity, "equity").complete)
        etf = {"name": "E", "symbol": "E", "expense_ratio": .002, "holdings": [{"x": 1}]}
        self.assertTrue(evaluate_fundamentals("a", etf, "etf").complete)
        self.assertFalse(evaluate_fundamentals("a", {"name": "E", "symbol": "E"}, "etf").complete)

        tech = {key: 1.0 for key in (
            "rsi_14", "sma_20", "sma_50", "return_1m_pct", "return_3m_pct",
            "return_6m_pct", "vs_sma20_pct", "vs_sma50_pct",
        )}
        self.assertTrue(evaluate_technicals("local", tech).complete)
        self.assertFalse(evaluate_technicals("local", {}).complete)

        item = NewsHeadline("1", "title", None, None, "publisher", now, "en", ["A"], "{}")
        self.assertTrue(evaluate_news("a", [item, item]).complete)
        self.assertEqual(1, len(evaluate_news("a", [item, item]).payload))
        self.assertFalse(evaluate_news("a", []).complete)

    def test_first_complete_and_best_single_partial(self):
        q = Quote(1, "USD", datetime.now(timezone.utc).isoformat(), "b")
        first = FakeAdapter("first", quote=Quote(0, "", "", "first"))
        second = FakeAdapter("second", quote=q)
        third = FakeAdapter("third", quote=q)
        selected = select_first_complete(
            [first, second, third], operation="quote", region="us", kind="equity",
            invoke=lambda a: a.get_quote("A", "USD"), evaluate=evaluate_quote,
        )
        self.assertEqual("second", selected.selected.provider)
        self.assertEqual(0, third.calls["quote"])

        p1 = FakeAdapter("p1", fundamentals={"name": "A", "symbol": "A", "market_cap": 1})
        p2 = FakeAdapter("p2", fundamentals={"name": "A", "symbol": "A", "market_cap": 1,
                                            "profit_margin": .1})
        partial = select_first_complete(
            [p1, p2], operation="fundamentals", region="us", kind="equity",
            invoke=lambda a: a.get_fundamentals("A", "equity"),
            evaluate=lambda p, v: evaluate_fundamentals(p, v, "equity"),
        )
        self.assertEqual("p2", partial.selected.provider)
        self.assertFalse(partial.selected.complete)

        complete_payload = {
            "name": "A", "symbol": "A", "market_cap": 1, "profit_margin": .1,
            "revenue_growth": .1, "current_ratio": 2,
        }
        complete = FakeAdapter("complete", fundamentals=complete_payload)
        unused = FakeAdapter("unused", fundamentals=complete_payload)
        selected = select_first_complete(
            [complete, unused], operation="fundamentals", region="us", kind="equity",
            invoke=lambda a: a.get_fundamentals("A", "equity"),
            evaluate=lambda p, v: evaluate_fundamentals(p, v, "equity"),
        )
        self.assertEqual("complete", selected.selected.provider)
        self.assertEqual(0, unused.calls["fundamentals"])

    def test_disabled_region_and_kind_support_are_classified_without_calls(self):
        q = Quote(1, "USD", datetime.now(timezone.utc).isoformat(), "ok")
        disabled = FakeAdapter("disabled", enabled=False, quote=q)
        eu_only = FakeAdapter("eu-only", quote=q, regions={"eu"})
        etf_only = FakeAdapter("etf-only", quote=q, kinds={"etf"})
        good = FakeAdapter("good", quote=q)
        selection = select_first_complete(
            [disabled, eu_only, etf_only, good],
            operation="quote", region="us", kind="equity",
            invoke=lambda a: a.get_quote("A", "USD"), evaluate=evaluate_quote,
        )
        self.assertEqual(["disabled", "unsupported", "unsupported", "complete"],
                         [attempt.status for attempt in selection.attempts])
        self.assertEqual(0, disabled.calls["quote"])
        self.assertEqual(0, eu_only.calls["quote"])
        self.assertEqual(0, etf_only.calls["quote"])
        self.assertEqual(1, good.calls["quote"])

    def test_successful_empty_news_falls_through(self):
        now = datetime.now(timezone.utc).isoformat()
        headline = NewsHeadline("n", "title", None, None, "publisher", now, "en", ["A"], "{}")
        empty = FakeAdapter("empty", news=[])
        useful = FakeAdapter("useful", news=[headline])
        selection = select_first_complete(
            [empty, useful], operation="news", region="us", kind="equity",
            invoke=lambda a: a.get_headlines(["A"]), evaluate=evaluate_news,
        )
        self.assertEqual("useful", selection.selected.provider)
        self.assertEqual(["incomplete", "complete"], [a.status for a in selection.attempts])

    def test_cache_preference(self):
        candidate = evaluate_fundamentals(
            "new", {"name": "A", "symbol": "A", "market_cap": 1}, "equity"
        )
        self.assertTrue(cache_wins(candidate.score + .1, "2026-01-01T00:00:00+00:00", candidate))
        self.assertTrue(cache_wins(candidate.score, "2099-01-01T00:00:00+00:00", candidate))
        self.assertFalse(cache_wins(candidate.score - .1, "2099-01-01T00:00:00+00:00", candidate))

    def test_ibkr_stub_never_enters_active_registry(self):
        previous = os.environ.get("IBKR_ENABLED")
        os.environ["IBKR_ENABLED"] = "1"
        try:
            service = MarketRefreshService("/tmp/unused-tradai-test.sqlite")
            self.assertTrue(service.ibkr.enabled())
            for region in ("us", "eu"):
                self.assertNotIn(
                    "ibkr", [adapter.name for adapter in service._historical_registry(region)]
                )
        finally:
            if previous is None:
                os.environ.pop("IBKR_ENABLED", None)
            else:
                os.environ["IBKR_ENABLED"] = previous

    def test_provider_http_errors_do_not_expose_credentials(self):
        response = Mock(status_code=500, headers={})
        adapter = MarketauxNewsAdapter("super-secret-token")
        with patch("infrastructure.adapters.marketaux.requests.get", return_value=response):
            with self.assertRaises(Exception) as raised:
                adapter.get_headlines(["AAPL"])
        self.assertNotIn("super-secret-token", str(raised.exception))
        self.assertEqual("transient", raised.exception.kind)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)

    def tearDown(self):
        os.unlink(self.path)

    def test_rate_state_survives_new_limiter_and_honours_headers(self):
        service = MarketRefreshService(self.path)
        conn = service.connect()
        adapter = FakeAdapter("vendor")
        first = PersistentRateLimiter(conn)
        first.before_call(adapter)
        first.observe_headers("vendor", {
            "X-RateLimit-Limit": "10", "X-RateLimit-Remaining": "0",
            "Retry-After": "60",
        })
        conn.close()

        conn = service.connect()
        row = conn.execute(
            "SELECT window_count, observed_limit, observed_remaining, cooldown_until "
            "FROM provider_rate_state WHERE provider='vendor'"
        ).fetchone()
        self.assertEqual(1, row["window_count"])
        self.assertEqual(10, row["observed_limit"])
        self.assertEqual(0, row["observed_remaining"])
        self.assertIsNotNone(row["cooldown_until"])
        with self.assertRaises(Exception) as raised:
            PersistentRateLimiter(conn).before_call(adapter)
        self.assertEqual("quota-deferred", raised.exception.kind)
        conn.close()

    def test_rate_environment_overrides_persist_and_pace(self):
        service = MarketRefreshService(self.path)
        conn = service.connect()
        adapter = FakeAdapter("limited")
        os.environ["LIMITED_RATE_WINDOW_LIMIT"] = "1"
        os.environ["LIMITED_MIN_INTERVAL_SECONDS"] = "2"
        try:
            limiter = PersistentRateLimiter(conn)
            limiter.before_call(adapter)
            with patch("infrastructure.adapters.rate_state.time.sleep") as sleeper:
                # Remove the hard window override to isolate minimum-interval pacing.
                os.environ.pop("LIMITED_RATE_WINDOW_LIMIT")
                limiter.before_call(adapter)
                sleeper.assert_called_once()
            conn.close()

            conn = service.connect()
            os.environ["LIMITED_RATE_WINDOW_LIMIT"] = "2"
            with self.assertRaises(Exception) as raised:
                PersistentRateLimiter(conn).before_call(adapter)
            self.assertEqual("quota-deferred", raised.exception.kind)
            conn.close()
        finally:
            os.environ.pop("LIMITED_RATE_WINDOW_LIMIT", None)
            os.environ.pop("LIMITED_MIN_INTERVAL_SECONDS", None)

    def test_ingestion_statistics_include_missing_datasets(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE instruments (
                id INTEGER PRIMARY KEY, symbol TEXT, currency TEXT, region TEXT, name TEXT,
                kind TEXT, isin TEXT, mic TEXT
            );
            CREATE TABLE holdings (instrument_id INTEGER);
            CREATE TABLE tracker (instrument_id INTEGER, archived_at TEXT);
            INSERT INTO instruments VALUES (1, 'TEST', 'EUR', 'us', 'Test', 'equity', NULL, NULL);
            INSERT INTO holdings VALUES (1);
            """
        )
        conn.close()

        service = MarketRefreshService(self.path)
        conn = service.connect()
        instruments = service._instruments(conn)
        statistics = service._ingestion_statistics(
            conn,
            instruments,
            {operation: set() for operation in (
                "quote", "bars", "long_history", "fundamentals", "technicals", "news"
            )},
        )
        conn.close()

        self.assertEqual("instrument_datasets", statistics["unit"])
        self.assertEqual(3, statistics["layers"]["historical"]["missing"])
        for layer in ("fundamentals", "technicals", "news"):
            self.assertEqual(
                {"ingested": 0, "from_cache": 0, "missing": 1},
                statistics["layers"][layer],
            )

    def test_cadence_isolation_and_unchanged_bars(self):
        conn = sqlite3.connect(self.path)
        conn.executescript(
            """
            CREATE TABLE instruments (
                id INTEGER PRIMARY KEY, symbol TEXT, currency TEXT, region TEXT, name TEXT,
                kind TEXT, isin TEXT, mic TEXT
            );
            CREATE TABLE holdings (instrument_id INTEGER);
            CREATE TABLE tracker (instrument_id INTEGER, archived_at TEXT);
            INSERT INTO instruments VALUES (1, 'TEST', 'EUR', 'us', 'Test', 'equity', NULL, NULL);
            INSERT INTO holdings VALUES (1);
            """
        )
        conn.close()

        now = datetime.now(timezone.utc)
        bars = [
            Bar((now.date() - timedelta(days=130-i)).isoformat(), 1, 2, .5, float(i + 1), 10, "fake")
            for i in range(130)
        ]
        fundamentals = {
            "name": "Test", "symbol": "TEST", "market_cap": 1, "profit_margin": .1,
            "revenue_growth": .1, "current_ratio": 2, "as_of": now.isoformat(),
        }
        news = [NewsHeadline(
            "n1", "News", None, "https://example.test", "Publisher", now.isoformat(),
            "en", ["TEST"], "{}",
        )]
        adapter = FakeAdapter(
            "fake", quote=Quote(10, "EUR", now.isoformat(), "fake"), bars=bars,
            fundamentals=fundamentals, news=news,
        )

        class Service(MarketRefreshService):
            def _historical_registry(self, region): return [adapter]
            def _fundamentals_registry(self, region, kind): return [adapter]
            def _news_registry(self, region): return [adapter]

        service = Service(self.path)
        first_report = service.refresh()
        self.assertEqual(
            {"quote": 1, "bars": 1, "long_history": 2, "fundamentals": 1, "news": 1}, adapter.calls
        )
        self.assertEqual(
            {
                "historical": {
                    "ingested": 3, "from_cache": 0, "missing": 0,
                    "operations": {
                        "quote": {"ingested": 1, "from_cache": 0, "missing": 0},
                        "bars": {"ingested": 1, "from_cache": 0, "missing": 0},
                        "long_history": {"ingested": 1, "from_cache": 0, "missing": 0},
                    },
                },
                "fundamentals": {"ingested": 1, "from_cache": 0, "missing": 0},
                "technicals": {"ingested": 1, "from_cache": 0, "missing": 0},
                "news": {"ingested": 1, "from_cache": 0, "missing": 0},
            },
            first_report["statistics"]["layers"],
        )
        conn = service.connect()
        tech_updated = conn.execute(
            "SELECT updated_at FROM technicals WHERE instrument_id=1"
        ).fetchone()[0]
        conn.close()

        cached_report = service.refresh()
        self.assertEqual(2, adapter.calls["quote"])
        self.assertEqual(1, adapter.calls["bars"])
        self.assertEqual(1, adapter.calls["fundamentals"])
        self.assertEqual(1, adapter.calls["news"])
        for counts in cached_report["statistics"]["layers"].values():
            self.assertEqual(0, counts["ingested"])
            self.assertGreater(counts["from_cache"], 0)
            self.assertEqual(0, counts["missing"])

        conn = service.connect()
        conn.execute(
            "UPDATE ingestion_state SET next_due_at='2000-01-01T00:00:00+00:00' "
            "WHERE instrument_id=1 AND operation='bars'"
        )
        conn.commit()
        conn.close()
        service.refresh()
        self.assertEqual(2, adapter.calls["bars"])
        conn = service.connect()
        self.assertEqual(
            tech_updated,
            conn.execute("SELECT updated_at FROM technicals WHERE instrument_id=1").fetchone()[0],
        )
        sources = {
            row[0] for row in conn.execute("SELECT DISTINCT source FROM price_bars")
        }
        self.assertEqual({"fake"}, sources)
        conn.close()

    def test_long_history_weekly_cache_and_benchmark_failure_are_isolated(self):
        conn = sqlite3.connect(self.path)
        conn.executescript(
            """
            CREATE TABLE instruments (
                id INTEGER PRIMARY KEY, symbol TEXT, currency TEXT, region TEXT, name TEXT,
                kind TEXT, isin TEXT, mic TEXT
            );
            CREATE TABLE holdings (instrument_id INTEGER);
            CREATE TABLE tracker (instrument_id INTEGER, archived_at TEXT);
            INSERT INTO instruments VALUES (1, 'TEST', 'USD', 'us', 'Test', 'equity', NULL, NULL);
            INSERT INTO holdings VALUES (1);
            """
        )
        conn.close()
        now = datetime.now(timezone.utc)
        bars = [
            Bar((now.date() - timedelta(days=130-i)).isoformat(), 1, 2, .5, float(i + 1), 10, "fake")
            for i in range(130)
        ]
        full_points = [
            HistoricalPoint((now.date() - timedelta(days=days)).isoformat(), value)
            for days, value in ((2200, 50), (1400, 70), (300, 90), (0, 100))
        ]
        adapter = FakeAdapter(
            "fake", quote=Quote(10, "USD", now.isoformat(), "fake"), bars=bars,
            long_history=LongHistory(full_points, "USD", full_points[-1].point_date),
        )

        class Service(MarketRefreshService):
            def _historical_registry(self, region): return [adapter]
            def _fundamentals_registry(self, region, kind): return []
            def _news_registry(self, region): return []

        service = Service(self.path)
        first = service.refresh()
        self.assertEqual("ingested", first["benchmark_history"]["us"]["status"])
        self.assertEqual(2, adapter.calls["long_history"])
        cached = service.refresh()
        self.assertEqual("from_cache", cached["benchmark_history"]["us"]["status"])
        self.assertEqual(2, adapter.calls["long_history"])

        def degraded(symbol):
            if symbol == "SPY":
                return AdapterTransientError("benchmark outage")
            points = full_points[-2:]
            return LongHistory(points, "USD", points[-1].point_date)

        adapter.long_history = degraded
        conn = service.connect()
        before = conn.execute(
            "SELECT earliest_date FROM historical_series WHERE series_key='instrument:1'"
        ).fetchone()[0]
        conn.execute(
            "UPDATE ingestion_state SET next_due_at='2000-01-01T00:00:00+00:00' "
            "WHERE operation='long_history'"
        )
        conn.execute(
            "UPDATE benchmark_history_state SET next_due_at='2000-01-01T00:00:00+00:00'"
        )
        conn.commit()
        conn.close()

        degraded_report = service.refresh()
        conn = service.connect()
        after = conn.execute(
            "SELECT earliest_date FROM historical_series WHERE series_key='instrument:1'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(before, after)
        self.assertEqual("retained_cache", degraded_report["benchmark_history"]["us"]["status"])
        self.assertIn("benchmark outage", degraded_report["benchmark_history"]["us"]["error"])
        self.assertFalse(any("benchmark outage" in error for error in degraded_report["errors"]))

    def test_manual_ingest_retries_only_fundamentals_gaps(self):
        conn = sqlite3.connect(self.path)
        conn.executescript(
            """
            CREATE TABLE instruments (
                id INTEGER PRIMARY KEY, symbol TEXT, currency TEXT, region TEXT, name TEXT,
                kind TEXT, isin TEXT, mic TEXT
            );
            CREATE TABLE holdings (instrument_id INTEGER);
            CREATE TABLE tracker (instrument_id INTEGER, archived_at TEXT);
            INSERT INTO instruments VALUES
                (1, 'PART', 'EUR', 'us', 'Partial', 'equity', NULL, NULL),
                (2, 'FULL', 'EUR', 'us', 'Complete', 'equity', NULL, NULL);
            INSERT INTO holdings VALUES (1), (2);
            """
        )
        conn.close()
        now = datetime.now(timezone.utc).isoformat()

        def fundamentals(symbol):
            base = {"name": symbol, "symbol": symbol, "market_cap": 1, "as_of": now}
            if symbol == "FULL":
                base.update({
                    "profit_margin": .1, "revenue_growth": .1, "current_ratio": 2,
                })
            return base

        adapter = FakeAdapter("fund", fundamentals=fundamentals)

        class Service(MarketRefreshService):
            def _historical_registry(self, region): return [adapter]
            def _fundamentals_registry(self, region, kind): return [adapter]
            def _news_registry(self, region): return [adapter]

        service = Service(self.path)
        service.refresh()
        self.assertEqual(2, adapter.calls["fundamentals"])

        service.refresh(manual_gap_retry=True)
        self.assertEqual(3, adapter.calls["fundamentals"])
        conn = service.connect()
        conn.execute(
            "UPDATE ingestion_state SET last_attempt_at=?,next_due_at=? "
            "WHERE instrument_id=1 AND operation='fundamentals'",
            (
                (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
                (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        service.refresh()
        self.assertEqual(4, adapter.calls["fundamentals"])

        conn = service.connect()
        rows = {
            row["instrument_id"]: row
            for row in conn.execute(
                "SELECT instrument_id,cadence_seconds FROM ingestion_state "
                "WHERE operation='fundamentals'"
            )
        }
        states = {
            row["instrument_id"]: row["completeness_state"]
            for row in conn.execute(
                "SELECT instrument_id,completeness_state FROM fundamentals"
            )
        }
        conn.close()
        self.assertEqual(24 * 60 * 60, rows[1]["cadence_seconds"])
        self.assertEqual(7 * 24 * 60 * 60, rows[2]["cadence_seconds"])
        self.assertEqual({1: "partial", 2: "complete"}, states)

    def test_existing_complete_fmp_cache_survives_active_provider_partial(self):
        conn = sqlite3.connect(self.path)
        conn.executescript(
            """
            CREATE TABLE instruments (
                id INTEGER PRIMARY KEY, symbol TEXT, currency TEXT, region TEXT, name TEXT,
                kind TEXT, isin TEXT, mic TEXT
            );
            CREATE TABLE holdings (instrument_id INTEGER);
            CREATE TABLE tracker (instrument_id INTEGER, archived_at TEXT);
            INSERT INTO instruments VALUES
                (1, 'LEGACY', 'EUR', 'us', 'Legacy', 'equity', NULL, NULL);
            INSERT INTO holdings VALUES (1);
            """
        )
        conn.close()
        adapter = FakeAdapter(
            "active",
            fundamentals={"name": "Legacy", "symbol": "LEGACY", "market_cap": 1},
        )

        class Service(MarketRefreshService):
            def _historical_registry(self, region): return [adapter]
            def _fundamentals_registry(self, region, kind): return [adapter]
            def _news_registry(self, region): return [adapter]

        service = Service(self.path)
        conn = service.connect()
        conn.execute(
            """
            INSERT INTO fundamentals
                (instrument_id,payload_json,source,as_of,completeness_state,
                 coverage_score,missing_fields_json,updated_at)
            VALUES (1,'{}','fmp','2026-01-01T00:00:00+00:00','complete',1.0,'[]',
                    '2026-01-01T00:00:00+00:00')
            """
        )
        conn.commit()
        conn.close()

        service.refresh(manual_gap_retry=True)
        service.refresh(manual_gap_retry=True)
        self.assertEqual(1, adapter.calls["fundamentals"])
        conn = service.connect()
        snapshot = conn.execute(
            "SELECT source,completeness_state FROM fundamentals WHERE instrument_id=1"
        ).fetchone()
        state = conn.execute(
            "SELECT cadence_seconds FROM ingestion_state "
            "WHERE instrument_id=1 AND operation='fundamentals'"
        ).fetchone()
        conn.close()
        self.assertEqual("fmp", snapshot["source"])
        self.assertEqual("complete", snapshot["completeness_state"])
        self.assertEqual(7 * 24 * 60 * 60, state["cadence_seconds"])

    def test_mixed_book_sqlite_scenario_degrades_per_dataset(self):
        conn = sqlite3.connect(self.path)
        conn.executescript(
            """
            CREATE TABLE instruments (
                id INTEGER PRIMARY KEY, symbol TEXT, currency TEXT, region TEXT, name TEXT,
                kind TEXT, isin TEXT, mic TEXT
            );
            CREATE TABLE holdings (instrument_id INTEGER);
            CREATE TABLE tracker (instrument_id INTEGER, archived_at TEXT);
            INSERT INTO instruments VALUES
                (1, 'US_EQ', 'EUR', 'us', 'US Equity', 'equity', NULL, NULL),
                (2, 'EU_ETF.DE', 'EUR', 'eu', 'EU ETF', 'etf', NULL, 'XETR'),
                (3, 'US_ETF', 'EUR', 'us', 'US ETF', 'etf', NULL, NULL),
                (4, 'EU_EQ.PA', 'EUR', 'eu', 'EU Equity', 'equity', NULL, 'XPAR');
            INSERT INTO holdings VALUES (1), (2);
            INSERT INTO tracker VALUES (3, NULL), (4, NULL);
            """
        )
        conn.close()
        now = datetime.now(timezone.utc)

        def quote(symbol):
            if symbol == "US_EQ":
                return AdapterTransientError("primary quote outage")
            return Quote(10, "EUR", now.isoformat(), "primary")

        def bars(symbol):
            count = 40 if symbol == "EU_ETF.DE" else 130
            return [
                Bar((now.date() - timedelta(days=count-i)).isoformat(), 1, 2, .5,
                    float(i + 1), 10, "primary")
                for i in range(count)
            ]

        def complete_bars(symbol):
            return [
                Bar((now.date() - timedelta(days=130-i)).isoformat(), 1, 2, .5,
                    float(i + 1), 10, "fallback")
                for i in range(130)
            ]

        hist_primary = FakeAdapter("hist-primary", quote=quote, bars=bars)
        hist_fallback = FakeAdapter(
            "hist-fallback", quote=Quote(11, "EUR", now.isoformat(), "fallback"),
            bars=complete_bars,
        )
        fund_disabled = FakeAdapter("fund-disabled", enabled=False)

        def fund_partial(symbol):
            if symbol == "US_ETF":
                return AdapterTransientError("fundamentals outage")
            base = {"name": symbol, "symbol": symbol, "as_of": now.isoformat()}
            if symbol == "US_EQ":
                base.update({
                    "market_cap": 1, "profit_margin": .1,
                    "revenue_growth": .1, "current_ratio": 2,
                })
            elif symbol == "EU_ETF.DE":
                base["expense_ratio"] = .002
            else:
                base["market_cap"] = 1
            return base

        def fund_fallback(symbol):
            kind = "etf" if "ETF" in symbol else "equity"
            base = {"name": symbol, "symbol": symbol, "as_of": now.isoformat()}
            if kind == "etf":
                base.update({"aum": 100, "holdings": [{"symbol": "X"}]})
            else:
                base.update({
                    "pe_ratio": 10, "roe": .1, "earnings_growth": .1,
                    "debt_to_equity": .2,
                })
            return base

        fund_primary = FakeAdapter("fund-primary", fundamentals=fund_partial)
        fund_second = FakeAdapter("fund-fallback", fundamentals=fund_fallback)
        news_quota = FakeAdapter("news-quota", news=[])
        news_fallback = FakeAdapter(
            "news-fallback",
            news=lambda symbol: [NewsHeadline(
                f"{symbol}-n", f"{symbol} news", None, "https://example.test",
                "Publisher", now.isoformat(), "en", [symbol], "{}",
            )],
        )

        class Service(MarketRefreshService):
            def _historical_registry(self, region):
                return [hist_primary, hist_fallback]

            def _fundamentals_registry(self, region, kind):
                return [fund_disabled, fund_primary, fund_second]

            def _news_registry(self, region):
                return [news_quota, news_fallback] if region == "us" else [news_quota]

        service = Service(self.path)
        conn = service.connect()
        old = (now - timedelta(days=2)).isoformat()
        conn.execute(
            "INSERT INTO news_items (external_id,title,fetched_at,adapter_source) "
            "VALUES ('cache:eu','cached EU news',?,'cache-news')", (old,)
        )
        news_id = int(conn.execute("SELECT id FROM news_items WHERE external_id='cache:eu'").fetchone()[0])
        for iid in (2, 4):
            conn.execute(
                "INSERT INTO news_item_instruments (news_item_id,instrument_id) VALUES (?,?)",
                (news_id, iid),
            )
            conn.execute(
                """
                INSERT INTO ingestion_state
                    (instrument_id,operation,cadence_seconds,attempts,last_success_at,
                     selected_source,coverage_score,missing_fields_json,gap_streak,
                     next_due_at,updated_at)
                VALUES (?,'news',86400,1,?,'cache-news',1.0,'[]',0,?,?)
                """, (iid, old, old, old)
            )
        conn.execute(
            """
            INSERT INTO provider_rate_state
                (provider,window_started_at,window_count,cooldown_until,updated_at)
            VALUES ('news-quota',?,1,?,?)
            """, (now.isoformat(), (now + timedelta(minutes=10)).isoformat(), now.isoformat())
        )
        conn.commit()
        conn.close()

        report = service.refresh()
        self.assertTrue(report["ok"])
        self.assertEqual(
            {"ingested": 12, "from_cache": 0, "missing": 0},
            {k: report["statistics"]["layers"]["historical"][k]
             for k in ("ingested", "from_cache", "missing")},
        )
        self.assertEqual(
            {"ingested": 2, "from_cache": 2, "missing": 0},
            report["statistics"]["layers"]["news"],
        )
        conn = service.connect()
        self.assertEqual(
            "hist-fallback",
            conn.execute("SELECT source FROM quotes WHERE instrument_id=1").fetchone()[0],
        )
        self.assertEqual(
            {"hist-fallback"},
            {r[0] for r in conn.execute("SELECT DISTINCT source FROM price_bars WHERE instrument_id=2")},
        )
        self.assertEqual(4, conn.execute("SELECT COUNT(*) FROM fundamentals").fetchone()[0])
        self.assertEqual(
            "fund-primary",
            conn.execute("SELECT source FROM fundamentals WHERE instrument_id=1").fetchone()[0],
        )
        self.assertEqual(
            "fund-fallback",
            conn.execute("SELECT source FROM fundamentals WHERE instrument_id=3").fetchone()[0],
        )
        self.assertEqual(
            {"cache-news"},
            {r[0] for r in conn.execute(
                "SELECT DISTINCT n.adapter_source FROM news_items n "
                "JOIN news_item_instruments l ON l.news_item_id=n.id WHERE l.instrument_id=4"
            )},
        )
        self.assertEqual(
            {"news-fallback"},
            {r[0] for r in conn.execute(
                "SELECT DISTINCT n.adapter_source FROM news_items n "
                "JOIN news_item_instruments l ON l.news_item_id=n.id WHERE l.instrument_id=3"
            )},
        )
        self.assertEqual(4, conn.execute("SELECT COUNT(*) FROM technicals").fetchone()[0])
        conn.close()


if __name__ == "__main__":
    unittest.main()
