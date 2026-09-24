from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from adapters.errors import SymbolNotFoundError
from adapters.finnhub import FinnhubHistoricalAdapter
from adapters.frankfurter import FrankfurterFxAdapter
from adapters.ibkr_stub import IbkrHistoricalAdapter
from adapters.marketaux import MarketauxNewsAdapter
from adapters.yfinance_hist import YFinanceHistoricalAdapter
from technicals import BarClose, compute_features


def resolve_region(symbol: str, region: str | None) -> str:
    if region in ("eu", "us"):
        return region
    if "." in symbol:
        return "eu"
    return "us"


class MarketRefreshService:
    def __init__(self, db_path: str, *, news_interval_seconds: int) -> None:
        self.db_path = db_path
        self.news_interval_seconds = news_interval_seconds
        self.finnhub = FinnhubHistoricalAdapter(os.environ.get("FINNHUB_API_KEY", ""))
        self.yfinance = YFinanceHistoricalAdapter()
        self.fx = FrankfurterFxAdapter()
        self.ibkr = IbkrHistoricalAdapter(os.environ.get("IBKR_ENABLED", "0") == "1")
        self.news = MarketauxNewsAdapter(os.environ.get("MARKETAUX_API_TOKEN", ""))

    def connect(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS quotes (
                instrument_id INTEGER PRIMARY KEY,
                price REAL NOT NULL,
                currency TEXT NOT NULL,
                as_of TEXT NOT NULL,
                source TEXT NOT NULL,
                raw_json TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS price_bars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument_id INTEGER NOT NULL,
                bar_date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL NOT NULL,
                volume REAL,
                source TEXT NOT NULL,
                UNIQUE (instrument_id, bar_date)
            );
            CREATE TABLE IF NOT EXISTS fx_rates (
                base_currency TEXT PRIMARY KEY,
                quote_currency TEXT NOT NULL,
                rate REAL NOT NULL,
                as_of TEXT NOT NULL,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                snippet TEXT,
                url TEXT,
                source_name TEXT,
                published_at TEXT,
                language TEXT,
                raw_json TEXT,
                fetched_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS news_item_instruments (
                news_item_id INTEGER NOT NULL,
                instrument_id INTEGER NOT NULL,
                PRIMARY KEY (news_item_id, instrument_id),
                FOREIGN KEY (news_item_id) REFERENCES news_items(id) ON DELETE CASCADE,
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS technicals (
                instrument_id INTEGER PRIMARY KEY,
                as_of TEXT NOT NULL,
                features_json TEXT NOT NULL,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS ingest_reports (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS news_refresh_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_run_at TEXT NOT NULL,
                last_success_at TEXT,
                report_json TEXT
            );
            CREATE TABLE IF NOT EXISTS news_symbol_aliases (
                instrument_id INTEGER PRIMARY KEY,
                query_symbol TEXT NOT NULL,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );
            """
        )
        return conn

    def refresh(self, *, force_news: bool = False) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        results: dict = {
            "ok": True,
            "stage": 4,
            "updated_at": now,
            "instruments": [],
            "fx": [],
            "news": {
                "fetched": 0,
                "linked": 0,
                "skipped": False,
                "reason": None,
                "per_symbol": {},
            },
            "technicals": [],
            "errors": [],
            "symbol_not_found": [],
            "ibkr_stub": not self.ibkr.enabled(),
            "marketaux_enabled": self.news.enabled(),
        }

        with self.connect() as conn:
            # 0019: both books ingest. Quotes, bars and technicals are free at this size,
            # so a tracked name is monitored exactly like a holding. News is the exception —
            # see _refresh_news, which stays holdings-only to protect the Marketaux quota.
            instruments = conn.execute(
                """
                SELECT i.id, i.symbol, i.currency, i.region, i.name, i.kind, i.isin,
                       CASE WHEN h.instrument_id IS NOT NULL THEN 'portfolio' ELSE 'tracker' END AS book
                FROM instruments i
                LEFT JOIN holdings h ON h.instrument_id = i.id
                LEFT JOIN tracker t ON t.instrument_id = i.id AND t.archived_at IS NULL
                WHERE h.instrument_id IS NOT NULL OR t.instrument_id IS NOT NULL
                ORDER BY i.symbol COLLATE NOCASE
                """
            ).fetchall()

            currencies = {"EUR"}
            for inst in instruments:
                currencies.add((inst["currency"] or "EUR").upper())
                item = {
                    "instrument_id": inst["id"],
                    "symbol": inst["symbol"],
                    "book": inst["book"],
                    "region": None,
                    "quote_source": None,
                    "bars": 0,
                    "error": None,
                }
                region = resolve_region(inst["symbol"], inst["region"])
                item["region"] = region
                try:
                    adapter = self._adapter_for(region)
                    quote = adapter.get_quote(inst["symbol"], inst["currency"] or "EUR")
                    conn.execute(
                        """
                        INSERT INTO quotes (instrument_id, price, currency, as_of, source, raw_json, updated_at)
                        VALUES (?, ?, ?, ?, ?, NULL, ?)
                        ON CONFLICT(instrument_id) DO UPDATE SET
                            price=excluded.price,
                            currency=excluded.currency,
                            as_of=excluded.as_of,
                            source=excluded.source,
                            updated_at=excluded.updated_at
                        """,
                        (
                            inst["id"],
                            quote.price,
                            quote.currency,
                            quote.as_of,
                            quote.source,
                            now,
                        ),
                    )
                    item["quote_source"] = quote.source
                    item["price"] = quote.price

                    try:
                        # 210 calendar days clears the 126-trading-day lookback return_6m_pct
                        # needs (0020), with enough margin for weekends/holidays.
                        bars = adapter.get_bars(inst["symbol"], days=210)
                        bars_source = adapter.name
                    except Exception as bar_exc:  # noqa: BLE001
                        # Finnhub free tier blocks candles — fall back to yfinance for OHLCV.
                        if adapter is self.finnhub:
                            try:
                                bars = self.yfinance.get_bars(inst["symbol"], days=210)
                                bars_source = self.yfinance.name
                                item["bars_fallback"] = f"yfinance after {bar_exc}"
                            except Exception as yf_exc:  # noqa: BLE001
                                item["bars"] = 0
                                item["bars_error"] = f"{bar_exc}; yfinance: {yf_exc}"
                                results["errors"].append(
                                    f"{inst['symbol']} bars: {bar_exc}; yfinance: {yf_exc}"
                                )
                                bars = None
                        else:
                            item["bars"] = 0
                            item["bars_error"] = str(bar_exc)
                            results["errors"].append(f"{inst['symbol']} bars: {bar_exc}")
                            bars = None

                    if bars is not None:
                        for bar in bars:
                            if bar.close is None or bar.close != bar.close:  # NaN
                                continue
                            conn.execute(
                                """
                                INSERT INTO price_bars
                                    (instrument_id, bar_date, open, high, low, close, volume, source)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(instrument_id, bar_date) DO UPDATE SET
                                    open=excluded.open,
                                    high=excluded.high,
                                    low=excluded.low,
                                    close=excluded.close,
                                    volume=excluded.volume,
                                    source=excluded.source
                                """,
                                (
                                    inst["id"],
                                    bar.bar_date,
                                    bar.open,
                                    bar.high,
                                    bar.low,
                                    bar.close,
                                    bar.volume,
                                    bar.source or bars_source,
                                ),
                            )
                        item["bars"] = len(bars)
                        item["bars_source"] = bars_source
                except SymbolNotFoundError as exc:
                    item["error"] = str(exc)
                    item["error_kind"] = "symbol_not_found"
                    results["symbol_not_found"].append(
                        {
                            "instrument_id": inst["id"],
                            "symbol": inst["symbol"],
                            "book": inst["book"],
                            "vendor": exc.vendor,
                            "detail": exc.detail,
                        }
                    )
                    results["errors"].append(f"{inst['symbol']}: symbol not found ({exc.vendor})")
                except Exception as exc:  # noqa: BLE001 — keep other instruments refreshing
                    item["error"] = str(exc)
                    item["error_kind"] = "error"
                    results["errors"].append(f"{inst['symbol']}: {exc}")
                results["instruments"].append(item)

            for cur in sorted(currencies):
                try:
                    rate, as_of = self.fx.rate_to_eur(cur)
                    conn.execute(
                        """
                        INSERT INTO fx_rates (base_currency, quote_currency, rate, as_of, source, updated_at)
                        VALUES (?, 'EUR', ?, ?, ?, ?)
                        ON CONFLICT(base_currency) DO UPDATE SET
                            quote_currency=excluded.quote_currency,
                            rate=excluded.rate,
                            as_of=excluded.as_of,
                            source=excluded.source,
                            updated_at=excluded.updated_at
                        """,
                        (cur, rate, as_of, self.fx.name, now),
                    )
                    results["fx"].append({"base": cur, "rate": rate, "as_of": as_of})
                except Exception as exc:  # noqa: BLE001
                    results["errors"].append(f"FX {cur}: {exc}")

            # Technics from whatever bars we have (fresh or cached).
            for inst in instruments:
                try:
                    rows = conn.execute(
                        """
                        SELECT bar_date, close FROM price_bars
                        WHERE instrument_id = ?
                        ORDER BY bar_date ASC
                        """,
                        (inst["id"],),
                    ).fetchall()
                    bars = [BarClose(bar_date=r["bar_date"], close=float(r["close"])) for r in rows]
                    features = compute_features(bars)
                    conn.execute(
                        """
                        INSERT INTO technicals (instrument_id, as_of, features_json, source, updated_at)
                        VALUES (?, ?, ?, 'local', ?)
                        ON CONFLICT(instrument_id) DO UPDATE SET
                            as_of=excluded.as_of,
                            features_json=excluded.features_json,
                            source=excluded.source,
                            updated_at=excluded.updated_at
                        """,
                        (
                            inst["id"],
                            features.get("as_of_bar") or now,
                            json.dumps(features),
                            now,
                        ),
                    )
                    results["technicals"].append(
                        {
                            "instrument_id": inst["id"],
                            "symbol": inst["symbol"],
                            "rsi_14": features.get("rsi_14"),
                            "sma_20": features.get("sma_20"),
                            "bar_count": features.get("bar_count"),
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    results["errors"].append(f"{inst['symbol']} technicals: {exc}")

            # News — per-symbol Marketaux calls (free tier: 3 articles/request).
            self._refresh_news(conn, instruments, results, now, force=force_news)

            conn.execute(
                """
                INSERT INTO ingest_reports (id, payload_json, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (json.dumps(results), now),
            )

            conn.commit()

        results["ok"] = True
        return results

    def _refresh_news(
        self,
        conn: sqlite3.Connection,
        instruments: list,
        results: dict,
        now: str,
        *,
        force: bool = False,
    ) -> None:
        if not instruments:
            results["news"]["skipped"] = True
            results["news"]["reason"] = "no holdings"
            return
        if not self.news.enabled():
            results["news"]["skipped"] = True
            results["news"]["reason"] = "MARKETAUX_API_TOKEN not set"
            return

        # Free tier is 100 req/day — one book pass per day is enough; SQLite is the cache.
        meta = conn.execute(
            "SELECT last_run_at, last_success_at, report_json FROM news_refresh_meta WHERE id = 1"
        ).fetchone()
        if not force and self.news_interval_seconds > 0 and meta is not None:
            try:
                last = str(meta["last_run_at"])
                last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - last_dt).total_seconds()
                if age < self.news_interval_seconds:
                    cached = {}
                    if meta["report_json"]:
                        try:
                            cached = json.loads(meta["report_json"])
                        except json.JSONDecodeError:
                            cached = {}
                    results["news"]["skipped"] = True
                    results["news"]["reason"] = (
                        f"cached ({int(age)}s old; refresh every {self.news_interval_seconds}s)"
                    )
                    results["news"]["completed_at"] = last
                    results["news"]["fetched"] = int(cached.get("fetched") or 0)
                    results["news"]["linked"] = int(cached.get("linked") or 0)
                    results["news"]["per_symbol"] = cached.get("per_symbol") or {}
                    results["news"]["from_cache"] = True
                    return
            except (TypeError, ValueError):
                pass

        fetched = 0
        linked = 0
        per_symbol: dict[str, dict] = {}
        any_ok = False

        for inst in instruments:
            symbol = str(inst["symbol"])
            instrument_id = int(inst["id"])
            name = inst["name"]
            kind = inst["kind"] if "kind" in inst.keys() else None
            isin = inst["isin"] if "isin" in inst.keys() else None
            query_sym = MarketauxNewsAdapter.query_symbol(symbol)
            used_sym = query_sym
            fallback = None
            headlines: list = []

            def _fetch(sym: str) -> list:
                try:
                    return self.news.get_headlines([sym], limit=3)
                except Exception as exc:  # noqa: BLE001
                    results["errors"].append(f"news {symbol} via {sym}: {exc}")
                    return []

            # Prefer cached Marketaux query symbol (e.g. VVMX.DE → REMX) to skip entity search.
            alias_row = conn.execute(
                "SELECT query_symbol, source FROM news_symbol_aliases WHERE instrument_id = ?",
                (instrument_id,),
            ).fetchone()
            if alias_row is not None:
                used_sym = str(alias_row["query_symbol"])
                fallback = str(alias_row["source"])
                headlines = _fetch(used_sym)

            if not headlines and used_sym != query_sym:
                # Stale alias — try the book listing once.
                used_sym = query_sym
                fallback = None
                headlines = _fetch(query_sym)
            elif not headlines and alias_row is None:
                headlines = _fetch(query_sym)

            if not headlines:
                try:
                    alt = self.news.resolve_alternate_symbol(query_sym, name)
                except Exception as exc:  # noqa: BLE001
                    results["errors"].append(f"news resolve {symbol}: {exc}")
                    alt = None
                if alt:
                    used_sym = alt
                    fallback = "listing"
                    headlines = _fetch(alt)

            if not headlines and MarketauxNewsAdapter.looks_like_etf(kind, name):
                try:
                    us_twin = self.news.resolve_us_etf_fallback(query_sym, name, isin)
                except Exception as exc:  # noqa: BLE001
                    results["errors"].append(f"news us_etf {symbol}: {exc}")
                    us_twin = None
                if us_twin and us_twin != used_sym:
                    used_sym = us_twin
                    fallback = "us_etf"
                    headlines = _fetch(us_twin)

            if headlines and used_sym != query_sym and fallback:
                conn.execute(
                    """
                    INSERT INTO news_symbol_aliases (instrument_id, query_symbol, source, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(instrument_id) DO UPDATE SET
                        query_symbol=excluded.query_symbol,
                        source=excluded.source,
                        updated_at=excluded.updated_at
                    """,
                    (instrument_id, used_sym, fallback, now),
                )

            count = 0
            for h in headlines:
                conn.execute(
                    """
                    INSERT INTO news_items
                        (external_id, title, snippet, url, source_name, published_at, language, raw_json, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(external_id) DO UPDATE SET
                        title=excluded.title,
                        snippet=excluded.snippet,
                        url=excluded.url,
                        source_name=excluded.source_name,
                        published_at=excluded.published_at,
                        language=excluded.language,
                        raw_json=excluded.raw_json,
                        fetched_at=excluded.fetched_at
                    """,
                    (
                        h.external_id,
                        h.title,
                        h.snippet,
                        h.url,
                        h.source_name,
                        h.published_at,
                        h.language,
                        h.raw_json,
                        now,
                    ),
                )
                row = conn.execute(
                    "SELECT id FROM news_items WHERE external_id = ?", (h.external_id,)
                ).fetchone()
                if row is None:
                    continue
                news_id = int(row["id"])
                fetched += 1
                count += 1
                any_ok = True
                conn.execute(
                    """
                    INSERT OR IGNORE INTO news_item_instruments (news_item_id, instrument_id)
                    VALUES (?, ?)
                    """,
                    (news_id, instrument_id),
                )
                linked += 1

            per_symbol[symbol] = {
                "ok": True,
                "count": count,
                "query_symbol": used_sym,
                "resolved": used_sym != query_sym,
                "fallback": fallback,
            }

        report = {
            "fetched": fetched,
            "linked": linked,
            "per_symbol": per_symbol,
            "completed_at": now,
        }
        results["news"]["fetched"] = fetched
        results["news"]["linked"] = linked
        results["news"]["per_symbol"] = per_symbol
        results["news"]["completed_at"] = now
        results["news"]["from_cache"] = False

        # Stamp last_run even on partial/API failure so we don't burn the free tier.
        conn.execute(
            """
            INSERT INTO news_refresh_meta (id, last_run_at, last_success_at, report_json)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                last_run_at=excluded.last_run_at,
                last_success_at=COALESCE(excluded.last_success_at, news_refresh_meta.last_success_at),
                report_json=excluded.report_json
            """,
            (now, now if any_ok else None, json.dumps(report)),
        )

    def _adapter_for(self, region: str):
        if self.ibkr.enabled():
            return self.ibkr
        if region == "us":
            if self.finnhub.enabled():
                return self.finnhub
            # Degrade to yfinance when Finnhub key missing so Stage 3 still works.
            return self.yfinance
        return self.yfinance
