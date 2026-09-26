from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from domain.providers.base import CandidateResult, Selection, select_first_complete
from infrastructure.adapters.alpha_vantage import (
    AlphaVantageEquityFundamentalsAdapter,
    AlphaVantageEtfFundamentalsAdapter,
)
from infrastructure.adapters.finnhub import FinnhubHistoricalAdapter
from infrastructure.adapters.finnhub_news import FinnhubNewsAdapter
from infrastructure.adapters.frankfurter import FrankfurterFxAdapter
from infrastructure.adapters.fundamentals import FinnhubFundamentalsAdapter, YFinanceFundamentalsAdapter
from infrastructure.adapters.ibkr_stub import IbkrHistoricalAdapter
from infrastructure.adapters.local_technicals import LocalTechnicalsAdapter
from infrastructure.adapters.marketaux import MarketauxNewsAdapter
from infrastructure.adapters.rate_state import PersistentRateLimiter
from infrastructure.adapters.yfinance_hist import YFinanceHistoricalAdapter
from domain.ingestion import (
    cache_wins,
    evaluate_bars,
    evaluate_fundamentals,
    evaluate_long_history,
    evaluate_news,
    evaluate_quote,
    evaluate_technicals,
    long_history_cache_wins,
)
from domain.technicals import BarClose

QUOTE_CADENCE = 15 * 60
BARS_CADENCE = 24 * 60 * 60
LONG_HISTORY_CADENCE = 7 * 24 * 60 * 60
NEWS_CADENCE = 24 * 60 * 60
FUNDAMENTALS_CADENCE = 7 * 24 * 60 * 60
FUNDAMENTALS_GAP_CADENCE = 24 * 60 * 60
FX_CADENCE = 12 * 60 * 60
DISPLAY_CURRENCIES = {"EUR", "USD", "GBP", "CHF"}


def resolve_region(symbol: str, region: str | None) -> str:
    if region in ("eu", "us"):
        return region
    return "eu" if "." in symbol else "us"


class MarketRefreshService:
    """Four independently due layers over ordered, outcome-oriented adapter registries."""

    def __init__(self, db_path: str, *, news_interval_seconds: int = NEWS_CADENCE) -> None:
        self.db_path = db_path
        self.news_interval_seconds = news_interval_seconds or NEWS_CADENCE
        finnhub_key = os.environ.get("FINNHUB_API_KEY", "")
        self.finnhub = FinnhubHistoricalAdapter(finnhub_key)
        self.yfinance = YFinanceHistoricalAdapter()
        self.fx = FrankfurterFxAdapter()
        # Kept visible for compatibility, but deliberately excluded from active registries:
        # enabling an unimplemented stub must never displace a working route.
        self.ibkr = IbkrHistoricalAdapter(os.environ.get("IBKR_ENABLED", "0") == "1")
        self.marketaux = MarketauxNewsAdapter(os.environ.get("MARKETAUX_API_TOKEN", ""))
        self.finnhub_news = FinnhubNewsAdapter(finnhub_key)
        self.finnhub_fundamentals = FinnhubFundamentalsAdapter(finnhub_key)
        alpha_vantage_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
        self.alpha_vantage_equity = AlphaVantageEquityFundamentalsAdapter(alpha_vantage_key)
        self.alpha_vantage_etf = AlphaVantageEtfFundamentalsAdapter(alpha_vantage_key)
        self.yf_fundamentals = YFinanceFundamentalsAdapter()
        self.local_technicals = LocalTechnicalsAdapter()

    def connect(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS quotes (
                instrument_id INTEGER PRIMARY KEY, price REAL NOT NULL, currency TEXT NOT NULL,
                as_of TEXT NOT NULL, source TEXT NOT NULL, raw_json TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS price_bars (
                id INTEGER PRIMARY KEY AUTOINCREMENT, instrument_id INTEGER NOT NULL,
                bar_date TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL NOT NULL,
                volume REAL, source TEXT NOT NULL, UNIQUE (instrument_id, bar_date)
            );
            CREATE TABLE IF NOT EXISTS historical_series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_key TEXT NOT NULL UNIQUE,
                series_kind TEXT NOT NULL CHECK (series_kind IN ('instrument', 'benchmark')),
                instrument_id INTEGER,
                benchmark_region TEXT CHECK (benchmark_region IS NULL OR benchmark_region IN ('eu', 'us')),
                symbol TEXT NOT NULL, native_currency TEXT NOT NULL, source TEXT NOT NULL,
                resolution TEXT NOT NULL, earliest_date TEXT NOT NULL, as_of TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS historical_points (
                series_id INTEGER NOT NULL, point_date TEXT NOT NULL,
                adjusted_close REAL NOT NULL, resolution TEXT NOT NULL,
                PRIMARY KEY (series_id, point_date),
                FOREIGN KEY (series_id) REFERENCES historical_series(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_historical_points_date
                ON historical_points (series_id, point_date);
            CREATE TABLE IF NOT EXISTS benchmark_history_state (
                region TEXT PRIMARY KEY CHECK (region IN ('eu', 'us')),
                symbol TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TEXT, last_success_at TEXT, next_due_at TEXT,
                last_error TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fx_rates (
                base_currency TEXT PRIMARY KEY, quote_currency TEXT NOT NULL, rate REAL NOT NULL,
                as_of TEXT NOT NULL, source TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, external_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL, snippet TEXT, url TEXT, source_name TEXT, published_at TEXT,
                language TEXT, raw_json TEXT, fetched_at TEXT NOT NULL, adapter_source TEXT
            );
            CREATE TABLE IF NOT EXISTS news_item_instruments (
                news_item_id INTEGER NOT NULL, instrument_id INTEGER NOT NULL,
                PRIMARY KEY (news_item_id, instrument_id),
                FOREIGN KEY (news_item_id) REFERENCES news_items(id) ON DELETE CASCADE,
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS technicals (
                instrument_id INTEGER PRIMARY KEY, as_of TEXT NOT NULL, features_json TEXT NOT NULL,
                source TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS fundamentals (
                instrument_id INTEGER PRIMARY KEY, payload_json TEXT NOT NULL, source TEXT NOT NULL,
                as_of TEXT NOT NULL, completeness_state TEXT NOT NULL,
                coverage_score REAL NOT NULL, missing_fields_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS ingestion_state (
                instrument_id INTEGER NOT NULL, operation TEXT NOT NULL,
                cadence_seconds INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TEXT, last_success_at TEXT, selected_source TEXT,
                coverage_score REAL, missing_fields_json TEXT NOT NULL DEFAULT '[]',
                gap_streak INTEGER NOT NULL DEFAULT 0, next_due_at TEXT,
                last_input_fingerprint TEXT, updated_at TEXT NOT NULL,
                PRIMARY KEY (instrument_id, operation),
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_ingestion_due
                ON ingestion_state (operation, next_due_at, last_success_at);
            CREATE TABLE IF NOT EXISTS provider_rate_state (
                provider TEXT PRIMARY KEY, window_started_at TEXT, window_count INTEGER NOT NULL DEFAULT 0,
                last_call_at TEXT, cooldown_until TEXT, observed_limit INTEGER,
                observed_remaining INTEGER, observed_reset_at TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ingest_reports (
                id INTEGER PRIMARY KEY CHECK (id = 1), payload_json TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS news_refresh_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1), last_run_at TEXT NOT NULL,
                last_success_at TEXT, report_json TEXT
            );
            CREATE TABLE IF NOT EXISTS news_symbol_aliases (
                instrument_id INTEGER PRIMARY KEY, query_symbol TEXT NOT NULL,
                source TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );
            """
        )
        self._ensure_column(conn, "news_items", "adapter_source", "TEXT")
        return conn

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, name: str, declaration: str) -> None:
        names = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
        if name not in names:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def refresh(
        self, *, force_news: bool = False, manual_gap_retry: bool = False
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        ingested: dict[str, set[int]] = {
            operation: set()
            for operation in ("quote", "bars", "long_history", "fundamentals", "technicals", "news")
        }
        results: dict[str, Any] = {
            "ok": True,
            "stage": 4,
            "updated_at": now_iso,
            "instruments": [],
            "fx": [],
            "news": {"fetched": 0, "linked": 0, "skipped": False, "reason": None, "per_symbol": {}},
            "fundamentals": [],
            "technicals": [],
            "errors": [],
            "symbol_not_found": [],
            "ibkr_stub": not self.ibkr.enabled(),
            "ibkr_routed": False,
            "marketaux_enabled": self.marketaux.enabled(),
            "benchmark_history": {},
        }
        with self.connect() as conn:
            instruments = self._instruments(conn)
            limiter = PersistentRateLimiter(conn)
            item_by_id: dict[int, dict[str, Any]] = {}
            for inst in instruments:
                item = {
                    "instrument_id": int(inst["id"]), "symbol": inst["symbol"], "book": inst["book"],
                    "region": resolve_region(inst["symbol"], inst["region"]),
                    "quote_source": None, "bars": 0, "error": None,
                }
                results["instruments"].append(item)
                item_by_id[int(inst["id"])] = item

            # Every invocation is a quote tick. Other datasets obey their own due clocks.
            for inst in self._oldest_first(conn, instruments, "quote"):
                if self._refresh_quote(
                    conn, limiter, inst, item_by_id[int(inst["id"])], results, now
                ):
                    ingested["quote"].add(int(inst["id"]))

            changed_bars: set[int] = set()
            for inst in self._oldest_first(conn, instruments, "bars"):
                if self._due(conn, int(inst["id"]), "bars", now):
                    accepted, changed = self._refresh_bars(
                        conn, limiter, inst, item_by_id[int(inst["id"])], results, now
                    )
                    if accepted:
                        ingested["bars"].add(int(inst["id"]))
                    if changed:
                        changed_bars.add(int(inst["id"]))

            for inst in self._oldest_first(conn, instruments, "long_history"):
                iid = int(inst["id"])
                if self._due(conn, iid, "long_history", now):
                    if self._refresh_long_history(
                        conn, limiter, inst, item_by_id[iid], results, now
                    ):
                        ingested["long_history"].add(iid)

            for region in sorted({resolve_region(i["symbol"], i["region"]) for i in instruments}):
                self._refresh_benchmark_history(conn, limiter, region, results, now)

            for inst in self._oldest_first(conn, instruments, "fundamentals"):
                iid = int(inst["id"])
                gap = self._fundamentals_gap(conn, iid)
                if self._fundamentals_due(conn, iid, now) or (manual_gap_retry and gap):
                    if self._refresh_fundamentals(conn, limiter, inst, results, now):
                        ingested["fundamentals"].add(iid)

            news_due = 0
            for inst in self._oldest_first(conn, instruments, "news"):
                if force_news or self._due(conn, int(inst["id"]), "news", now):
                    news_due += 1
                    if self._refresh_news(conn, limiter, inst, results, now):
                        ingested["news"].add(int(inst["id"]))
            if news_due == 0:
                results["news"]["skipped"] = True
                results["news"]["reason"] = "cached; no instrument news is due"

            for inst in instruments:
                iid = int(inst["id"])
                no_technical_state = self._state(conn, iid, "technicals") is None
                if iid in changed_bars or no_technical_state:
                    if self._refresh_technicals(conn, inst, results, now):
                        ingested["technicals"].add(iid)

            self._refresh_fx(conn, instruments, results, now)
            results["statistics"] = self._ingestion_statistics(conn, instruments, ingested)
            self._event("ingest_statistics", statistics=results["statistics"])
            conn.execute(
                """
                INSERT INTO ingest_reports (id, payload_json, updated_at) VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (json.dumps(results), now_iso),
            )
            conn.commit()
        return results

    def _instruments(self, conn: sqlite3.Connection) -> list[sqlite3.Row]:
        return conn.execute(
            """
            SELECT i.id, i.symbol, i.currency, i.region, i.name, i.kind, i.isin, i.mic,
                   CASE WHEN h.instrument_id IS NOT NULL THEN 'portfolio' ELSE 'tracker' END AS book
            FROM instruments i
            LEFT JOIN holdings h ON h.instrument_id = i.id
            LEFT JOIN tracker t ON t.instrument_id = i.id AND t.archived_at IS NULL
            WHERE h.instrument_id IS NOT NULL OR t.instrument_id IS NOT NULL
            ORDER BY i.symbol COLLATE NOCASE
            """
        ).fetchall()

    def _oldest_first(
        self, conn: sqlite3.Connection, instruments: list[sqlite3.Row], operation: str
    ) -> list[sqlite3.Row]:
        success = {
            int(row["instrument_id"]): str(row["last_success_at"] or "")
            for row in conn.execute(
                "SELECT instrument_id, last_success_at FROM ingestion_state WHERE operation = ?",
                (operation,),
            )
        }
        return sorted(instruments, key=lambda i: (success.get(int(i["id"]), ""), str(i["symbol"]).lower()))

    def _historical_registry(self, region: str) -> list[Any]:
        return [self.finnhub, self.yfinance] if region == "us" else [self.yfinance]

    def _long_history_registry(self, region: str) -> list[Any]:
        # Adjusted close (splits + distributions) is an explicit Yahoo contract here.
        # Reuse the historical extension point so deterministic test registries remain isolated;
        # unsupported adapters (Finnhub in production) are classified without being called.
        return self._historical_registry(region)

    def _fundamentals_registry(self, region: str, kind: str) -> list[Any]:
        if kind == "etf":
            return [self.alpha_vantage_etf, self.yf_fundamentals]
        if region == "us":
            return [self.finnhub_fundamentals, self.alpha_vantage_equity]
        return [self.alpha_vantage_equity, self.yf_fundamentals]

    def _news_registry(self, region: str) -> list[Any]:
        return [self.marketaux, self.finnhub_news] if region == "us" else [self.marketaux]

    def _technicals_registry(self) -> list[Any]:
        # Extension point for sourced indicators; local remains first and normally complete.
        return [self.local_technicals]

    def _select(
        self,
        conn: sqlite3.Connection,
        limiter: PersistentRateLimiter,
        adapters: list[Any],
        *,
        operation: str,
        region: str,
        kind: str,
        invoke: Callable[[Any], Any],
        evaluate: Callable[[str, Any], CandidateResult],
    ) -> Selection:
        selection = select_first_complete(
            adapters, operation=operation, region=region, kind=kind,
            invoke=invoke, evaluate=evaluate,
            before_call=limiter.before_call, after_call=limiter.after_call,
        )
        for attempt in selection.attempts:
            if attempt.status in ("rate-limited", "quota-deferred"):
                # Classified failures carry a provider response or policy retry delay.
                limiter.defer(attempt.provider, attempt.retry_after)
        return selection

    def _refresh_quote(
        self, conn: sqlite3.Connection, limiter: PersistentRateLimiter, inst: sqlite3.Row,
        item: dict[str, Any], results: dict[str, Any], now: datetime,
    ) -> bool:
        iid, symbol = int(inst["id"]), str(inst["symbol"])
        region, kind = resolve_region(symbol, inst["region"]), str(inst["kind"] or "equity")
        selection = self._select(
            conn, limiter, self._historical_registry(region), operation="quote",
            region=region, kind=kind,
            invoke=lambda adapter: adapter.get_quote(symbol, inst["currency"] or "EUR"),
            evaluate=evaluate_quote,
        )
        candidate = selection.selected
        accepted = False
        if candidate and candidate.complete:
            existing = conn.execute(
                "SELECT q.as_of, q.price, q.source, s.coverage_score "
                "FROM quotes q LEFT JOIN ingestion_state s "
                "ON s.instrument_id=q.instrument_id AND s.operation='quote' WHERE q.instrument_id=?",
                (iid,),
            ).fetchone()
            if not existing or not cache_wins(existing["coverage_score"], existing["as_of"], candidate):
                quote = candidate.payload
                conn.execute(
                    """
                    INSERT INTO quotes (instrument_id, price, currency, as_of, source, raw_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, NULL, ?)
                    ON CONFLICT(instrument_id) DO UPDATE SET price=excluded.price,
                        currency=excluded.currency, as_of=excluded.as_of, source=excluded.source,
                        updated_at=excluded.updated_at
                    """,
                    (iid, quote.price, quote.currency, quote.as_of, candidate.provider, now.isoformat()),
                )
                item.update({"quote_source": candidate.provider, "price": quote.price})
                accepted = True
            else:
                item.update({
                    "quote_source": existing["source"], "price": float(existing["price"]),
                    "from_cache": True,
                })
                self._event("retained_cache", symbol=symbol, operation="quote",
                            candidate_source=candidate.provider)
        self._record_state(conn, iid, "quote", QUOTE_CADENCE, selection, accepted, now)
        self._report_attempts(symbol, "quote", selection, results)
        return accepted

    def _refresh_bars(
        self, conn: sqlite3.Connection, limiter: PersistentRateLimiter, inst: sqlite3.Row,
        item: dict[str, Any], results: dict[str, Any], now: datetime,
    ) -> tuple[bool, bool]:
        iid, symbol = int(inst["id"]), str(inst["symbol"])
        region, kind = resolve_region(symbol, inst["region"]), str(inst["kind"] or "equity")
        selection = self._select(
            conn, limiter, self._historical_registry(region), operation="bars",
            region=region, kind=kind,
            invoke=lambda adapter: adapter.get_bars(symbol, days=370),
            evaluate=evaluate_bars,
        )
        candidate = selection.selected
        accepted = False
        changed = False
        if candidate and candidate.payload:
            existing_bars = conn.execute(
                "SELECT bar_date, open, high, low, close, volume, source FROM price_bars "
                "WHERE instrument_id=? ORDER BY bar_date", (iid,)
            ).fetchall()
            existing_eval = evaluate_bars(
                str(existing_bars[-1]["source"]) if existing_bars else "cache", existing_bars
            )
            state = self._state(conn, iid, "bars")
            score = state["coverage_score"] if state else existing_eval.score if existing_bars else None
            if not existing_bars or not cache_wins(score, existing_eval.as_of, candidate):
                fingerprint = self._bars_fingerprint(candidate.payload)
                previous_fingerprint = state["last_input_fingerprint"] if state else None
                conn.execute("DELETE FROM price_bars WHERE instrument_id=?", (iid,))
                conn.executemany(
                    """
                    INSERT INTO price_bars
                        (instrument_id, bar_date, open, high, low, close, volume, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [(
                        iid, bar.bar_date, bar.open, bar.high, bar.low, bar.close, bar.volume,
                        candidate.provider,
                    ) for bar in candidate.payload],
                )
                item.update({"bars": len(candidate.payload), "bars_source": candidate.provider})
                accepted = True
                changed = fingerprint != previous_fingerprint
                self._record_state(
                    conn, iid, "bars", BARS_CADENCE, selection, True, now,
                    fingerprint=fingerprint,
                )
            else:
                item.update({"bars": len(existing_bars), "bars_source": existing_eval.provider})
                self._event("retained_cache", symbol=symbol, operation="bars",
                            candidate_source=candidate.provider)
        if not accepted:
            self._record_state(conn, iid, "bars", BARS_CADENCE, selection, False, now)
        self._report_attempts(symbol, "bars", selection, results)
        return accepted, changed

    def _refresh_long_history(
        self, conn: sqlite3.Connection, limiter: PersistentRateLimiter, inst: sqlite3.Row,
        item: dict[str, Any], results: dict[str, Any], now: datetime,
    ) -> bool:
        iid, symbol = int(inst["id"]), str(inst["symbol"])
        region, kind = resolve_region(symbol, inst["region"]), str(inst["kind"] or "equity")
        selection = self._select(
            conn, limiter, self._long_history_registry(region), operation="long_history",
            region=region, kind=kind,
            invoke=lambda adapter: adapter.get_long_history(symbol, inst["currency"] or "EUR"),
            evaluate=evaluate_long_history,
        )
        existing = self._history_series(conn, f"instrument:{iid}")
        candidate = selection.selected
        accepted = False
        if candidate and candidate.complete and not long_history_cache_wins(
            str(existing["earliest_date"]) if existing else None,
            str(existing["as_of"]) if existing else None,
            candidate,
        ):
            history = candidate.payload
            self._replace_history_series(
                conn, series_key=f"instrument:{iid}", series_kind="instrument",
                instrument_id=iid, benchmark_region=None, symbol=symbol,
                currency=history.currency or str(inst["currency"] or "EUR"),
                source=candidate.provider, points=history.points, now=now,
            )
            accepted = True
            item.update({
                "long_history": len(history.points),
                "long_history_source": candidate.provider,
            })
        elif existing:
            count = int(conn.execute(
                "SELECT COUNT(*) FROM historical_points WHERE series_id=?", (existing["id"],)
            ).fetchone()[0])
            item.update({
                "long_history": count,
                "long_history_source": existing["source"],
                "long_history_from_cache": True,
            })
            if candidate:
                self._event("retained_cache", symbol=symbol, operation="long_history",
                            candidate_source=candidate.provider)
        self._record_state(
            conn, iid, "long_history",
            LONG_HISTORY_CADENCE if accepted or existing else 0,
            selection, accepted, now
        )
        self._report_attempts(symbol, "long_history", selection, results)
        return accepted

    def _refresh_benchmark_history(
        self, conn: sqlite3.Connection, limiter: PersistentRateLimiter, region: str,
        results: dict[str, Any], now: datetime,
    ) -> None:
        symbol, currency = ("SPY", "USD") if region == "us" else ("EXSA.DE", "EUR")
        existing = self._history_series(conn, f"benchmark:{region}")
        state = conn.execute(
            "SELECT * FROM benchmark_history_state WHERE region=?", (region,)
        ).fetchone()
        if state and state["next_due_at"] and not self._time_due_at(state["next_due_at"], now):
            results["benchmark_history"][region] = {
                "symbol": symbol, "status": "from_cache", "source": existing["source"] if existing else None,
                "as_of": existing["as_of"] if existing else None,
            }
            return

        selection = self._select(
            conn, limiter, self._long_history_registry(region), operation="long_history",
            region=region, kind="etf",
            invoke=lambda adapter: adapter.get_long_history(symbol, currency),
            evaluate=evaluate_long_history,
        )
        candidate = selection.selected
        accepted = False
        error = None
        if candidate and candidate.complete and not long_history_cache_wins(
            str(existing["earliest_date"]) if existing else None,
            str(existing["as_of"]) if existing else None,
            candidate,
        ):
            history = candidate.payload
            self._replace_history_series(
                conn, series_key=f"benchmark:{region}", series_kind="benchmark",
                instrument_id=None, benchmark_region=region, symbol=symbol,
                currency=history.currency or currency, source=candidate.provider,
                points=history.points, now=now,
            )
            accepted = True
            existing = self._history_series(conn, f"benchmark:{region}")
        elif not candidate or not candidate.complete:
            details = [a.detail or a.status for a in selection.attempts if a.status != "complete"]
            error = "; ".join(details) or "no complete adjusted history"
        elif existing:
            error = "provider response had inferior coverage; retained cache"

        next_due = now + timedelta(seconds=LONG_HISTORY_CADENCE if existing else 0)
        conn.execute(
            """
            INSERT INTO benchmark_history_state
                (region,symbol,attempts,last_attempt_at,last_success_at,next_due_at,last_error,updated_at)
            VALUES (?,?,1,?,?,?,?,?)
            ON CONFLICT(region) DO UPDATE SET symbol=excluded.symbol,
                attempts=benchmark_history_state.attempts+1,
                last_attempt_at=excluded.last_attempt_at,
                last_success_at=COALESCE(excluded.last_success_at,benchmark_history_state.last_success_at),
                next_due_at=excluded.next_due_at,last_error=excluded.last_error,
                updated_at=excluded.updated_at
            """,
            (region, symbol, now.isoformat(), now.isoformat() if accepted else None,
             next_due.isoformat(), error, now.isoformat()),
        )
        status = "ingested" if accepted else ("retained_cache" if existing else "failed")
        results["benchmark_history"][region] = {
            "symbol": symbol, "status": status,
            "source": existing["source"] if existing else None,
            "as_of": existing["as_of"] if existing else None,
            "error": error,
        }

    @staticmethod
    def _history_series(conn: sqlite3.Connection, series_key: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM historical_series WHERE series_key=?", (series_key,)
        ).fetchone()

    @staticmethod
    def _replace_history_series(
        conn: sqlite3.Connection, *, series_key: str, series_kind: str,
        instrument_id: int | None, benchmark_region: str | None, symbol: str,
        currency: str, source: str, points: list[Any], now: datetime,
    ) -> None:
        earliest, as_of = points[0].point_date, points[-1].point_date
        conn.execute(
            """
            INSERT INTO historical_series
                (series_key,series_kind,instrument_id,benchmark_region,symbol,native_currency,
                 source,resolution,earliest_date,as_of,updated_at)
            VALUES (?,?,?,?,?,?,?,'compact',?,?,?)
            ON CONFLICT(series_key) DO UPDATE SET series_kind=excluded.series_kind,
                instrument_id=excluded.instrument_id,benchmark_region=excluded.benchmark_region,
                symbol=excluded.symbol,native_currency=excluded.native_currency,
                source=excluded.source,resolution=excluded.resolution,
                earliest_date=excluded.earliest_date,as_of=excluded.as_of,
                updated_at=excluded.updated_at
            """,
            (series_key, series_kind, instrument_id, benchmark_region, symbol,
             currency.upper(), source, earliest, as_of, now.isoformat()),
        )
        series_id = int(conn.execute(
            "SELECT id FROM historical_series WHERE series_key=?", (series_key,)
        ).fetchone()[0])
        conn.execute("DELETE FROM historical_points WHERE series_id=?", (series_id,))
        conn.executemany(
            "INSERT INTO historical_points (series_id,point_date,adjusted_close,resolution) "
            "VALUES (?,?,?,?)",
            [(series_id, p.point_date, p.adjusted_close, p.resolution) for p in points],
        )

    def _refresh_fundamentals(
        self, conn: sqlite3.Connection, limiter: PersistentRateLimiter,
        inst: sqlite3.Row, results: dict[str, Any], now: datetime,
    ) -> bool:
        iid, symbol = int(inst["id"]), str(inst["symbol"])
        region, kind = resolve_region(symbol, inst["region"]), str(inst["kind"] or "equity")
        selection = self._select(
            conn, limiter, self._fundamentals_registry(region, kind), operation="fundamentals",
            region=region, kind=kind,
            invoke=lambda adapter: adapter.get_fundamentals(symbol, kind, inst["mic"]),
            evaluate=lambda provider, payload: evaluate_fundamentals(provider, payload, kind),
        )
        candidate = selection.selected
        accepted = False
        if candidate and candidate.payload:
            existing = conn.execute(
                "SELECT as_of, coverage_score FROM fundamentals WHERE instrument_id=?", (iid,)
            ).fetchone()
            if not existing or not cache_wins(existing["coverage_score"], existing["as_of"], candidate):
                conn.execute(
                    """
                    INSERT INTO fundamentals
                        (instrument_id, payload_json, source, as_of, completeness_state,
                         coverage_score, missing_fields_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(instrument_id) DO UPDATE SET payload_json=excluded.payload_json,
                        source=excluded.source, as_of=excluded.as_of,
                        completeness_state=excluded.completeness_state,
                        coverage_score=excluded.coverage_score,
                        missing_fields_json=excluded.missing_fields_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        iid, json.dumps(candidate.payload), candidate.provider,
                        candidate.as_of or now.isoformat(),
                        "complete" if candidate.complete else "partial",
                        candidate.score, json.dumps(candidate.missing_fields), now.isoformat(),
                    ),
                )
                accepted = True
            else:
                self._event("retained_cache", symbol=symbol, operation="fundamentals",
                            candidate_source=candidate.provider)
            results["fundamentals"].append({
                "instrument_id": iid, "symbol": symbol, "source": candidate.provider,
                "complete": candidate.complete, "score": candidate.score,
                "missing_fields": candidate.missing_fields, "accepted": accepted,
            })
        complete_cache = not self._fundamentals_gap(conn, iid)
        cadence = FUNDAMENTALS_CADENCE if complete_cache else FUNDAMENTALS_GAP_CADENCE
        self._record_state(
            conn, iid, "fundamentals", cadence, selection, accepted, now,
            effective_complete=complete_cache,
        )
        self._report_attempts(symbol, "fundamentals", selection, results)
        return accepted

    def _refresh_news(
        self, conn: sqlite3.Connection, limiter: PersistentRateLimiter,
        inst: sqlite3.Row, results: dict[str, Any], now: datetime,
    ) -> bool:
        iid, symbol = int(inst["id"]), str(inst["symbol"])
        region, kind = resolve_region(symbol, inst["region"]), str(inst["kind"] or "equity")
        selection = self._select(
            conn, limiter, self._news_registry(region), operation="news",
            region=region, kind=kind,
            invoke=lambda adapter: adapter.get_headlines([symbol], limit=3),
            evaluate=evaluate_news,
        )
        candidate = selection.selected
        accepted = False
        count = 0
        if candidate and candidate.payload:
            existing = conn.execute(
                """
                SELECT MAX(COALESCE(n.published_at, n.fetched_at)) AS as_of,
                       s.coverage_score
                FROM news_item_instruments nii
                JOIN news_items n ON n.id=nii.news_item_id
                LEFT JOIN ingestion_state s ON s.instrument_id=nii.instrument_id AND s.operation='news'
                WHERE nii.instrument_id=?
                """, (iid,)
            ).fetchone()
            if not existing or not existing["as_of"] or not cache_wins(
                existing["coverage_score"], existing["as_of"], candidate
            ):
                # A feed snapshot is one provider response. Links from older adapters are removed
                # so the accepted dataset is never field/item-merged across providers.
                conn.execute("DELETE FROM news_item_instruments WHERE instrument_id=?", (iid,))
                for headline in candidate.payload:
                    external_id = f"{candidate.provider}:{headline.external_id}"
                    conn.execute(
                        """
                        INSERT INTO news_items
                            (external_id, title, snippet, url, source_name, published_at,
                             language, raw_json, fetched_at, adapter_source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(external_id) DO UPDATE SET title=excluded.title,
                            snippet=excluded.snippet, url=excluded.url,
                            source_name=excluded.source_name, published_at=excluded.published_at,
                            language=excluded.language, raw_json=excluded.raw_json,
                            fetched_at=excluded.fetched_at, adapter_source=excluded.adapter_source
                        """,
                        (
                            external_id, headline.title, headline.snippet, headline.url,
                            headline.source_name, headline.published_at, headline.language,
                            headline.raw_json, now.isoformat(), candidate.provider,
                        ),
                    )
                    news_id = int(conn.execute(
                        "SELECT id FROM news_items WHERE external_id=?", (external_id,)
                    ).fetchone()[0])
                    conn.execute(
                        "INSERT OR IGNORE INTO news_item_instruments (news_item_id, instrument_id) "
                        "VALUES (?, ?)", (news_id, iid)
                    )
                    count += 1
                accepted = True
            else:
                self._event("retained_cache", symbol=symbol, operation="news",
                            candidate_source=candidate.provider)
        results["news"]["fetched"] += count
        results["news"]["linked"] += count
        results["news"]["per_symbol"][symbol] = {
            "ok": bool(candidate), "count": count,
            "source": candidate.provider if candidate else None, "accepted": accepted,
        }
        self._record_state(conn, iid, "news", self.news_interval_seconds, selection, accepted, now)
        self._report_attempts(symbol, "news", selection, results)
        return accepted

    def _refresh_technicals(
        self, conn: sqlite3.Connection, inst: sqlite3.Row,
        results: dict[str, Any], now: datetime,
    ) -> bool:
        iid, symbol = int(inst["id"]), str(inst["symbol"])
        rows = conn.execute(
            "SELECT bar_date, close FROM price_bars WHERE instrument_id=? ORDER BY bar_date", (iid,)
        ).fetchall()
        closes = [
            BarClose(bar_date=str(row["bar_date"]), close=float(row["close"])) for row in rows
        ]
        selection = select_first_complete(
            self._technicals_registry(), operation="technicals",
            region=resolve_region(symbol, inst["region"]), kind=str(inst["kind"] or "equity"),
            invoke=lambda adapter: adapter.get_technicals(symbol, closes),
            evaluate=evaluate_technicals,
        )
        candidate = selection.selected or evaluate_technicals("local", {})
        features = candidate.payload
        existing = conn.execute(
            "SELECT as_of FROM technicals WHERE instrument_id=?", (iid,)
        ).fetchone()
        accepted = bool(rows)
        if accepted:
            conn.execute(
                """
                INSERT INTO technicals (instrument_id, as_of, features_json, source, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id) DO UPDATE SET as_of=excluded.as_of,
                    features_json=excluded.features_json, source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                (
                    iid, features.get("as_of_bar") or now.isoformat(), json.dumps(features),
                    candidate.provider, now.isoformat(),
                ),
            )
        self._record_state(conn, iid, "technicals", 0, selection, accepted, now)
        results["technicals"].append({
            "instrument_id": iid, "symbol": symbol, "rsi_14": features.get("rsi_14"),
            "sma_20": features.get("sma_20"), "bar_count": features.get("bar_count"),
            "complete": candidate.complete, "missing_fields": candidate.missing_fields,
            "recomputed": True,
        })
        self._report_attempts(symbol, "technicals", selection, results)
        return accepted

    @staticmethod
    def _ingestion_statistics(
        conn: sqlite3.Connection,
        instruments: list[sqlite3.Row],
        ingested: dict[str, set[int]],
    ) -> dict[str, Any]:
        """Classify each active instrument dataset by what served this refresh."""
        instrument_ids = {int(inst["id"]) for inst in instruments}
        cached: dict[str, set[int]] = {
            "quote": {int(row[0]) for row in conn.execute("SELECT instrument_id FROM quotes")},
            "bars": {
                int(row[0])
                for row in conn.execute("SELECT DISTINCT instrument_id FROM price_bars")
            },
            "long_history": {
                int(row[0])
                for row in conn.execute(
                    "SELECT instrument_id FROM historical_series "
                    "WHERE series_kind='instrument' AND instrument_id IS NOT NULL"
                )
            },
            "fundamentals": {
                int(row[0]) for row in conn.execute("SELECT instrument_id FROM fundamentals")
            },
            "technicals": {
                int(row[0]) for row in conn.execute("SELECT instrument_id FROM technicals")
            },
            "news": {
                int(row[0])
                for row in conn.execute(
                    "SELECT DISTINCT instrument_id FROM news_item_instruments"
                )
            },
        }

        def operation_counts(operation: str) -> dict[str, int]:
            fresh = instrument_ids & ingested[operation]
            from_cache = (instrument_ids & cached[operation]) - fresh
            return {
                "ingested": len(fresh),
                "from_cache": len(from_cache),
                "missing": len(instrument_ids - fresh - from_cache),
            }

        operations = {
            operation: operation_counts(operation)
            for operation in ("quote", "bars", "long_history", "fundamentals", "technicals", "news")
        }

        def layer_counts(*operation_names: str) -> dict[str, int]:
            return {
                field: sum(operations[operation][field] for operation in operation_names)
                for field in ("ingested", "from_cache", "missing")
            }

        historical: dict[str, Any] = {
            **layer_counts("quote", "bars", "long_history"),
            "operations": {
                "quote": operations["quote"],
                "bars": operations["bars"],
                "long_history": operations["long_history"],
            },
        }
        return {
            "unit": "instrument_datasets",
            "layers": {
                "historical": historical,
                "fundamentals": layer_counts("fundamentals"),
                "technicals": layer_counts("technicals"),
                "news": layer_counts("news"),
            },
        }

    def _refresh_fx(
        self, conn: sqlite3.Connection, instruments: list[sqlite3.Row],
        results: dict[str, Any], now: datetime,
    ) -> None:
        currencies = DISPLAY_CURRENCIES | {
            str(inst["currency"] or "EUR").upper() for inst in instruments
        }
        currencies.update(
            str(row["currency"] or "EUR").upper()
            for row in conn.execute("SELECT DISTINCT currency FROM quotes").fetchall()
        )
        for key in ("cash_currency", "realized_gains_ytd_override_currency"):
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if row and row["value"]:
                currencies.add(str(row["value"]).upper())
        for currency in sorted(currencies):
            row = conn.execute(
                "SELECT updated_at FROM fx_rates WHERE base_currency=?", (currency,)
            ).fetchone()
            if row and not self._time_due(row["updated_at"], FX_CADENCE, now):
                continue
            try:
                rate, as_of = self.fx.rate_to_eur(currency)
                conn.execute(
                    """
                    INSERT INTO fx_rates
                        (base_currency, quote_currency, rate, as_of, source, updated_at)
                    VALUES (?, 'EUR', ?, ?, ?, ?)
                    ON CONFLICT(base_currency) DO UPDATE SET rate=excluded.rate,
                        as_of=excluded.as_of, source=excluded.source, updated_at=excluded.updated_at
                    """,
                    (currency, rate, as_of, self.fx.name, now.isoformat()),
                )
                results["fx"].append({"base": currency, "rate": rate, "as_of": as_of})
            except Exception as exc:
                results["errors"].append(f"FX {currency}: {exc}")

    def _due(
        self, conn: sqlite3.Connection, instrument_id: int, operation: str, now: datetime
    ) -> bool:
        row = self._state(conn, instrument_id, operation)
        if row is None or not row["next_due_at"]:
            return True
        try:
            due = datetime.fromisoformat(str(row["next_due_at"]).replace("Z", "+00:00"))
            return due <= now
        except ValueError:
            return True

    @staticmethod
    def _fundamentals_gap(conn: sqlite3.Connection, instrument_id: int) -> bool:
        row = conn.execute(
            "SELECT completeness_state FROM fundamentals WHERE instrument_id=?",
            (instrument_id,),
        ).fetchone()
        return row is None or str(row["completeness_state"]) != "complete"

    def _fundamentals_due(
        self, conn: sqlite3.Connection, instrument_id: int, now: datetime
    ) -> bool:
        if not self._fundamentals_gap(conn, instrument_id):
            return self._due(conn, instrument_id, "fundamentals", now)
        state = self._state(conn, instrument_id, "fundamentals")
        if state is None or not state["last_attempt_at"]:
            return True
        try:
            attempted = datetime.fromisoformat(
                str(state["last_attempt_at"]).replace("Z", "+00:00")
            )
            return attempted + timedelta(seconds=FUNDAMENTALS_GAP_CADENCE) <= now
        except ValueError:
            return True

    @staticmethod
    def _state(
        conn: sqlite3.Connection, instrument_id: int, operation: str
    ) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM ingestion_state WHERE instrument_id=? AND operation=?",
            (instrument_id, operation),
        ).fetchone()

    def _record_state(
        self, conn: sqlite3.Connection, instrument_id: int, operation: str,
        cadence: int, selection: Selection, accepted: bool, now: datetime,
        *, fingerprint: str | None = None, effective_complete: bool | None = None,
    ) -> None:
        previous = self._state(conn, instrument_id, operation)
        selected = selection.selected
        attempt_complete = bool(selected and selected.complete)
        complete = (
            effective_complete
            if effective_complete is not None
            else attempt_complete
        )
        gap_streak = 0 if complete else (int(previous["gap_streak"] or 0) if previous else 0) + 1
        next_due = now + timedelta(seconds=max(0, cadence))
        conn.execute(
            """
            INSERT INTO ingestion_state
                (instrument_id, operation, cadence_seconds, attempts, last_attempt_at,
                 last_success_at, selected_source, coverage_score, missing_fields_json,
                 gap_streak, next_due_at, last_input_fingerprint, updated_at)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instrument_id, operation) DO UPDATE SET
                cadence_seconds=excluded.cadence_seconds,
                attempts=ingestion_state.attempts + 1,
                last_attempt_at=excluded.last_attempt_at,
                last_success_at=COALESCE(excluded.last_success_at, ingestion_state.last_success_at),
                selected_source=COALESCE(excluded.selected_source, ingestion_state.selected_source),
                coverage_score=CASE WHEN excluded.selected_source IS NULL
                    THEN ingestion_state.coverage_score ELSE excluded.coverage_score END,
                missing_fields_json=CASE WHEN excluded.selected_source IS NULL
                    THEN ingestion_state.missing_fields_json ELSE excluded.missing_fields_json END,
                gap_streak=excluded.gap_streak, next_due_at=excluded.next_due_at,
                last_input_fingerprint=COALESCE(
                    excluded.last_input_fingerprint, ingestion_state.last_input_fingerprint
                ), updated_at=excluded.updated_at
            """,
            (
                instrument_id, operation, cadence, now.isoformat(),
                now.isoformat() if attempt_complete else None,
                selected.provider if selected and accepted else None,
                selected.score if selected and accepted else None,
                json.dumps(selected.missing_fields if selected else ["no_result"]),
                gap_streak, next_due.isoformat(), fingerprint, now.isoformat(),
            ),
        )
        if selected and not selected.complete:
            self._event(
                "partial_selection" if accepted else "source_gap",
                instrument_id=instrument_id, operation=operation,
                source=selected.provider, score=selected.score,
                missing_fields=selected.missing_fields,
                recurring=gap_streak >= 3, gap_streak=gap_streak,
            )
        elif selected is None:
            self._event(
                "source_gap", instrument_id=instrument_id, operation=operation,
                missing_fields=["no_result"], recurring=gap_streak >= 3, gap_streak=gap_streak,
            )

    def _report_attempts(
        self, symbol: str, operation: str, selection: Selection, results: dict[str, Any]
    ) -> None:
        if selection.selected and len(selection.attempts) > 1:
            prior = [
                attempt.provider for attempt in selection.attempts
                if attempt.provider != selection.selected.provider
                and attempt.status not in ("complete",)
            ]
            if prior:
                self._event(
                    "fallback", symbol=symbol, operation=operation,
                    from_providers=prior, selected_source=selection.selected.provider,
                    complete=selection.selected.complete,
                )
        for attempt in selection.attempts:
            if attempt.status in ("complete",):
                continue
            self._event(
                "quota_deferral" if attempt.status in ("quota-deferred", "rate-limited")
                else "adapter_attempt",
                symbol=symbol, operation=operation,
                provider=attempt.provider, status=attempt.status,
                score=attempt.score, missing_fields=attempt.missing_fields,
                detail=attempt.detail,
            )
            if attempt.status == "not-found":
                results["symbol_not_found"].append({
                    "symbol": symbol, "vendor": attempt.provider, "detail": attempt.detail,
                    "operation": operation,
                })
            if attempt.status not in ("disabled", "unsupported", "incomplete"):
                results["errors"].append(
                    f"{symbol} {operation} via {attempt.provider}: {attempt.detail or attempt.status}"
                )

    @staticmethod
    def _event(event: str, **values: Any) -> None:
        print(json.dumps({"service": "tradai-worker", "event": event, **values}), flush=True)

    @staticmethod
    def _bars_fingerprint(bars: list[Any]) -> str:
        material = "|".join(f"{bar.bar_date}:{bar.close}" for bar in bars)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _time_due(value: str, cadence: int, now: datetime) -> bool:
        try:
            then = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return (now - then).total_seconds() >= cadence
        except ValueError:
            return True

    @staticmethod
    def _time_due_at(value: str, now: datetime) -> bool:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")) <= now
        except ValueError:
            return True
