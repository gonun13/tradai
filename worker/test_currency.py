from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from services.advisory import AdvisoryService
from services.refresh import MarketRefreshService


SCHEMA = """
CREATE TABLE instruments (id INTEGER PRIMARY KEY, symbol TEXT, isin TEXT, mic TEXT,
  currency TEXT, name TEXT, kind TEXT, region TEXT);
CREATE TABLE holdings (id INTEGER PRIMARY KEY, instrument_id INTEGER, quantity REAL,
  avg_cost REAL, total_cost REAL, notes TEXT, first_trade_date TEXT,
  open_lot_count INTEGER, realized_pnl_native REAL);
CREATE TABLE quotes (instrument_id INTEGER PRIMARY KEY, price REAL, currency TEXT,
  as_of TEXT, source TEXT, updated_at TEXT);
CREATE TABLE technicals (instrument_id INTEGER PRIMARY KEY, features_json TEXT, as_of TEXT);
CREATE TABLE fundamentals (instrument_id INTEGER PRIMARY KEY, payload_json TEXT, source TEXT,
  as_of TEXT, completeness_state TEXT, coverage_score REAL, missing_fields_json TEXT);
CREATE TABLE fx_rates (base_currency TEXT PRIMARY KEY, quote_currency TEXT, rate REAL,
  as_of TEXT, source TEXT, updated_at TEXT);
CREATE TABLE news_items (id INTEGER PRIMARY KEY, title TEXT, snippet TEXT, url TEXT,
  source_name TEXT, adapter_source TEXT, published_at TEXT, fetched_at TEXT);
CREATE TABLE news_item_instruments (news_item_id INTEGER, instrument_id INTEGER);
CREATE TABLE tracker (id INTEGER PRIMARY KEY, symbol TEXT, instrument_id INTEGER, name TEXT,
  added_at TEXT, updated_at TEXT, archived_at TEXT);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
CREATE TABLE realized_disposals (id INTEGER PRIMARY KEY, instrument_id INTEGER,
  sell_transaction_id INTEGER, trade_date TEXT, quantity REAL, proceeds REAL, cost REAL,
  realized_pnl REAL, currency TEXT, created_at TEXT, proceeds_eur REAL, cost_eur REAL,
  realized_pnl_eur REAL);
CREATE TABLE theses (id INTEGER PRIMARY KEY, instrument_id INTEGER, thesis TEXT,
  falsifiers_json TEXT, status TEXT, version INTEGER, source TEXT, agent_run_id INTEGER,
  created_at TEXT, approved_at TEXT, superseded_at TEXT);
"""


class CurrencyContextTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_context_uses_display_fields_quote_currency_and_no_partial_totals(self):
        self.conn.executemany(
            "INSERT INTO settings(key,value,updated_at) VALUES (?,?,'x')",
            [
                ("display_currency", "USD"),
                ("cash_amount", "20"),
                ("cash_currency", "GBP"),
                ("realized_gains_ytd_override_amount", "10"),
                ("realized_gains_ytd_override_currency", "CHF"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO fx_rates VALUES (?, 'EUR', ?, 'x', 'test', 'x')",
            [("USD", 0.8), ("GBP", 1.2), ("CHF", 1.05)],
        )
        self.conn.execute(
            "INSERT INTO instruments VALUES (1,'OWN',NULL,NULL,'GBP','Owned','equity','eu')"
        )
        self.conn.execute(
            "INSERT INTO holdings VALUES (1,1,10,10,100,NULL,'2025-01-01',1,0)"
        )
        self.conn.execute("INSERT INTO quotes VALUES (1,10,'CHF','x','test','x')")
        self.conn.commit()

        service = AdvisoryService(self.path)
        context = service._build_context(self.conn)
        holding = context["holdings"][0]
        self.assertEqual("USD", context["display_currency"])
        self.assertAlmostEqual(150.0, holding["cost_display"])
        self.assertAlmostEqual(131.25, holding["market_value_display"])
        self.assertAlmostEqual(131.25, context["portfolio_market_value_display"])
        self.assertAlmostEqual(30.0, context["cash_display"])
        self.assertAlmostEqual(13.13, context["realized_gains_ytd_display"])
        self.assertNotIn("cost_eur", holding)
        slim = service._slim_portfolio(context)
        lens = service._lens_state(context, {}, "combined")
        self.assertEqual("USD", slim["display_currency"])
        self.assertEqual("USD", lens["display_currency"])
        self.assertNotIn("_eur", str(slim))
        self.assertNotIn("_eur", str(lens))

        self.conn.execute(
            "INSERT INTO instruments VALUES (2,'NOFX',NULL,NULL,'CAD','Missing','equity','us')"
        )
        self.conn.execute(
            "INSERT INTO holdings VALUES (2,2,1,10,10,NULL,'2025-01-01',1,0)"
        )
        self.conn.execute("INSERT INTO quotes VALUES (2,12,'CAD','x','test','x')")
        self.conn.commit()
        incomplete = AdvisoryService(self.path)._build_context(self.conn)
        self.assertIsNone(incomplete["portfolio_market_value_display"])
        self.assertIsNone(incomplete["portfolio_cost_display"])
        self.assertTrue(all(h["weight_pct"] is None for h in incomplete["holdings"]))

    def test_realized_ytd_sums_locked_eur_and_converts_to_display(self):
        # 0031: a USD gain natively can be an EUR loss; only EUR -> display uses today's rate.
        year = datetime.now(timezone.utc).year
        self.conn.executemany(
            "INSERT INTO settings(key,value,updated_at) VALUES (?,?,'x')",
            [("display_currency", "USD")],
        )
        self.conn.execute("INSERT INTO fx_rates VALUES ('USD', 'EUR', 0.8, 'x', 'test', 'x')")
        self.conn.executemany(
            "INSERT INTO realized_disposals VALUES (?,1,?,?,1,0,0,?, 'USD','x',NULL,NULL,?)",
            [
                (1, 11, f"{year}-02-01", 50.0, -60.0),
                (2, 12, f"{year}-03-01", 10.0, 20.0),
                (3, 13, f"{year - 1}-03-01", 999.0, 999.0),
            ],
        )
        self.conn.commit()
        service = AdvisoryService(self.path)
        self.assertAlmostEqual(-50.0, service._realized_gains_ytd_display(self.conn, "USD"))
        self.assertAlmostEqual(-40.0, service._realized_gains_ytd_display(self.conn, "EUR"))

        self.conn.execute(
            "INSERT INTO realized_disposals VALUES (4,1,14,?,1,0,0,5,'USD','x',NULL,NULL,NULL)",
            (f"{year}-04-01",),
        )
        self.conn.commit()
        self.assertIsNone(service._realized_gains_ytd_display(self.conn, "USD"))

    def test_refresh_requests_books_quotes_settings_and_all_display_currencies(self):
        self.conn.execute(
            "INSERT INTO instruments VALUES (1,'ONE',NULL,NULL,'JPY','One','equity','us')"
        )
        self.conn.execute("INSERT INTO quotes VALUES (1,10,'CAD','x','test','x')")
        self.conn.executemany(
            "INSERT INTO settings(key,value,updated_at) VALUES (?,?,'x')",
            [("cash_currency", "GBP"), ("realized_gains_ytd_override_currency", "CHF")],
        )
        self.conn.commit()
        instruments = self.conn.execute("SELECT * FROM instruments").fetchall()
        service = MarketRefreshService(self.path)
        service.fx = Mock()
        service.fx.name = "fake"
        service.fx.rate_to_eur.side_effect = lambda code: (1.0, "2026-09-25")
        results = {"fx": [], "errors": []}
        service._refresh_fx(self.conn, instruments, results, datetime.now(timezone.utc))
        requested = {call.args[0] for call in service.fx.rate_to_eur.call_args_list}
        self.assertEqual({"EUR", "USD", "GBP", "CHF", "JPY", "CAD"}, requested)


if __name__ == "__main__":
    unittest.main()
