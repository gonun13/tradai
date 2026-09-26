"""
Layer cards (0027): deterministic, rounded, whitelisted — and the long-history series only
ever leaves as statistics.

Run: docker compose exec worker python test_digest.py
"""

from __future__ import annotations

import json
import unittest
from datetime import date, timedelta

from domain import digest


def daily(start: date, days: int, first: float, step: float) -> list[tuple[str, float]]:
    return [((start + timedelta(days=i)).isoformat(), first + step * i) for i in range(days)]


class HistoricalCardTests(unittest.TestCase):
    def test_statistics_from_points_and_benchmark(self):
        end = date(2026, 9, 25)
        # Six years of steady doubling-ish growth, then a year of daily points.
        points = [((end - timedelta(days=365 * y)).isoformat(), 100.0 * 1.2 ** (6 - y)) for y in range(6, 0, -1)]
        points += daily(end - timedelta(days=364), 365, 250.0, 0.2)
        bench = [((end - timedelta(days=365 * y)).isoformat(), 100.0 * 1.1 ** (6 - y)) for y in range(6, -1, -1)]

        card = digest.historical_card(points, benchmark=bench, benchmark_symbol="SPY")

        self.assertEqual("2026-09-25", card["as_of"])
        self.assertEqual("SPY", card["bench"])
        # 0028: the short-term moves the market read leans on.
        # Ends at 322.8 after +0.2/day, so 30 days back it was 316.8.
        self.assertAlmostEqual(6.0 / 316.8 * 100, card["ret_1m_pct"], delta=0.05)
        for key in ("ret_1m_pct", "ret_3m_pct", "ret_1y_pct", "cagr_3y_pct", "cagr_5y_pct", "max_dd_5y_pct",
                    "from_52w_high_pct", "range_52w_pos_pct", "vol_1y_pct",
                    "vs_bench_1y_pp", "vs_bench_5y_cagr_pp"):
            self.assertIn(key, card)
        self.assertEqual(0.0, card["from_52w_high_pct"])  # ends on its high
        self.assertEqual(100.0, card["range_52w_pos_pct"])
        # 0026: never the points themselves.
        self.assertLess(len(json.dumps(card)), 400)
        self.assertNotIn("2025-", json.dumps(card))

    def test_short_history_omits_what_it_cannot_know(self):
        end = date(2026, 9, 25)
        card = digest.historical_card(daily(end - timedelta(days=199), 200, 10.0, 0.01))
        self.assertNotIn("cagr_3y_pct", card)
        self.assertNotIn("cagr_5y_pct", card)
        self.assertNotIn("ret_1y_pct", card)
        self.assertNotIn("bench", card)

    def test_empty_or_bad_points(self):
        self.assertIsNone(digest.historical_card([]))
        self.assertIsNone(digest.historical_card([("2026-01-01", 0), ("bad", 5)]))


class FundamentalsCardTests(unittest.TestCase):
    def test_equity_whitelist_rounding_and_thesis(self):
        snapshot = {
            "payload": {
                "sector": "Industrials", "description": "x" * 2000, "market_cap": 12_345_678_901,
                "pe_ratio": 23.456789, "roe": 0.123456, "unknown_field": 1,
            },
            "completeness_state": "complete", "missing_fields": [],
            "as_of": "2026-09-25T16:25:13+00:00",
        }
        thesis = {"text": "Durable defence demand.", "falsifiers": ["Orders fall 2 quarters"], "version": 3}
        card = digest.fundamentals_card(snapshot, kind="equity", thesis=thesis)
        self.assertEqual(23.5, card["pe_ratio"])
        self.assertEqual(0.123, card["roe"])
        self.assertEqual(12.35, card["market_cap_bn"])
        self.assertEqual("2026-09-25", card["as_of"])
        self.assertEqual(3, card["thesis_v"])
        self.assertNotIn("description", card)
        self.assertNotIn("unknown_field", card)
        self.assertNotIn("missing", card)

    def test_etf_trims_holdings_and_sectors(self):
        snapshot = {
            "payload": {
                "category": "Defence", "aum": 2_500_000_000, "expense_ratio": 0.0055,
                "holdings": [{"symbol": f"H{i}", "holdingPercent": {"raw": 0.1 - i / 100}} for i in range(10)],
                "allocations": [{"industrials": {"raw": 0.6}}, {"technology": 0.3}, {"energy": 0.05}, {"utilities": 0.05}],
            },
            "completeness_state": "complete",
        }
        card = digest.fundamentals_card(snapshot, kind="etf", book="tracker")
        self.assertEqual(5, len(card["top_holdings"]))
        self.assertEqual("H0", card["top_holdings"][0]["n"])
        self.assertEqual(["industrials", "technology", "energy"], [s["n"] for s in card["top_sectors"]])
        self.assertEqual(2.5, card["aum_bn"])
        self.assertNotIn("thesis", card)  # tracked names have no thesis of record

    def test_missing_snapshot_still_carries_thesis(self):
        card = digest.fundamentals_card(None, kind="equity", thesis={"text": "T", "falsifiers": [], "version": 1})
        self.assertEqual({"thesis": "T", "thesis_v": 1}, card)
        self.assertIsNone(digest.fundamentals_card(None, kind="equity", book="tracker"))


class TechnicalsAndNewsTests(unittest.TestCase):
    def test_technicals_zones(self):
        card = digest.technicals_card({
            "as_of_bar": "2026-09-24", "computed_at": "x", "last_close": 291.79998779296875,
            "sma_20": 284.27999725341795, "rsi_14": 72.4, "vs_sma20_pct": 2.6452, "vs_sma50_pct": -1.328,
            "return_1m_pct": 0.0, "return_3m_pct": 2.8913898, "return_6m_pct": -5.87097,
        })
        self.assertEqual("overbought", card["rsi_zone"])
        self.assertEqual("mixed", card["trend"])
        self.assertEqual(2.9, card["r3m_pct"])
        for dropped in ("computed_at", "last_close", "sma_20"):
            self.assertNotIn(dropped, card)
        self.assertIsNone(digest.technicals_card(None))

    def test_news_dedupes_caps_and_splits_snippets(self):
        items = [
            {"title": "Big Order Won!", "snippet": "s" * 400, "source": "a", "published_at": "2026-09-20T10:00:00Z"},
            {"title": "big order won", "snippet": "dup", "source": "b", "published_at": "2026-09-19"},
        ] + [{"title": f"Item {i}", "published_at": "2026-09-1{i}"} for i in range(6)]
        card = digest.news_card(items)
        self.assertEqual(digest.NEWS_MAX, len(card))
        self.assertEqual("2026-09-20", card[0]["d"])
        self.assertLessEqual(len(card[0]["snip"]), digest.NEWS_SNIPPET_MAX)
        self.assertEqual(1, sum(1 for n in card if digest.news_key(n["title"]) == "big order won"))
        jev = digest.news_for_jev(card)
        self.assertTrue(all(isinstance(t, str) and "s" * 50 not in t for t in jev))


class HeadlineTapeTests(unittest.TestCase):
    def test_book_wide_newest_first_deduped_and_capped(self):
        subjects = [
            {"symbol": f"S{i}", "cards": {"news": [
                {"d": f"2026-09-{10 + i:02d}", "title": f"Story {i}"},
                {"d": "2026-09-01", "title": "Shared Macro Story"},
            ]}}
            for i in range(15)
        ]
        tape = digest.headline_tape(subjects)
        self.assertEqual(digest.HEADLINE_TAPE_MAX, len(tape))
        self.assertTrue(tape[0].startswith("2026-09-24 [S14]"))
        self.assertLessEqual(sum("Shared Macro Story" in t for t in tape), 1)
        self.assertEqual([], digest.headline_tape([{"symbol": "X", "cards": {}}]))


class BuildCardsTests(unittest.TestCase):
    def test_same_inputs_same_cards(self):
        subject = {
            "book": "portfolio", "kind": "equity", "quantity": 3.0, "avg_cost": 10.123456,
            "quote": {"price": 12.3456}, "pnl_pct": 21.98765, "weight_pct": 4.5678,
            "technicals": {"rsi_14": 50, "vs_sma20_pct": 1, "vs_sma50_pct": 2},
            "news": [{"title": "A"}],
        }
        a = digest.build_cards(subject)
        b = digest.build_cards(dict(subject))
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual(22.0, a["position"]["pnl_pct"])
        self.assertEqual(4.6, a["position"]["wgt"])


if __name__ == "__main__":
    unittest.main()
