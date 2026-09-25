"""
Materiality gate (0027): each trigger re-decides a subject; nothing material carries it.

Run: docker compose exec worker python test_materiality.py
"""

from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone

from digest import LENS_SCHEMA
from materiality import triggers

NOW = datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc)

CARDS = {
    "historical": {"ret_1y_pct": 10.0},
    "fundamentals": {"as_of": "2026-09-20", "coverage": "complete", "thesis": "T", "falsifiers": ["F"]},
    "technicals": {"trend": "above_both", "rsi_zone": "neutral", "rsi": 55.0},
    "news": [{"d": "2026-09-24", "title": "Old headline"}],
    "position": {"px": 100.0, "qty": 10.0, "open_lots": 2},
}


def prior(**overrides):
    record = {
        "schema": LENS_SCHEMA,
        "decided_at": (NOW - timedelta(days=1)).isoformat(),
        "cards": copy.deepcopy(CARDS),
        "mandate_hash": "m",
        "book_fp": "b",
        "price_at_rec": 100.0,
        "fallback": False,
    }
    record.update(overrides)
    return record


def check(cards=None, book="portfolio", **kwargs):
    args = {
        "book": book,
        "cards": cards if cards is not None else copy.deepcopy(CARDS),
        "mandate_hash": "m",
        "book_fp": "b",
        "prior": prior(),
        "now": NOW,
        "move_pct": 5.0,
        "carry_days": 7,
    }
    args.update(kwargs)
    return triggers(**args)


def changed(path, value):
    cards = copy.deepcopy(CARDS)
    layer, key = path
    if key is None:
        cards[layer] = value
    else:
        cards[layer][key] = value
    return cards


class MaterialityTests(unittest.TestCase):
    def test_nothing_material_carries(self):
        self.assertEqual([], check())
        # Small drifts that don't cross a zone or the move threshold still carry.
        cards = changed(("technicals", "rsi"), 61.0)
        cards["position"]["px"] = 104.0
        cards["historical"]["ret_1y_pct"] = 11.0
        self.assertEqual([], check(cards))

    def test_each_trigger(self):
        cases = {
            "news": changed(("news", None), [{"title": "Fresh headline"}, {"title": "Old headline"}]),
            "fundamentals": changed(("fundamentals", "as_of"), "2026-09-25"),
            "technicals_zone": changed(("technicals", "trend"), "mixed"),
            "price_move": changed(("position", "px"), 94.0),
            "thesis": changed(("fundamentals", "falsifiers"), ["F", "G"]),
            "no_thesis": changed(("fundamentals", "thesis"), None),
            "position": changed(("position", "qty"), 12.0),
        }
        for name, cards in cases.items():
            with self.subTest(name):
                self.assertEqual([name], check(cards))

    def test_book_level_and_record_triggers(self):
        self.assertEqual(["mandate"], check(mandate_hash="m2"))
        self.assertEqual(["cash"], check(book_fp="b2"))
        self.assertEqual(["stale"], check(prior=prior(decided_at=(NOW - timedelta(days=7)).isoformat())))
        self.assertEqual(["prior_fallback"], check(prior=prior(fallback=True)))
        self.assertEqual(["no_prior"], check(prior=None))
        self.assertEqual(["lens_schema"], check(prior=prior(schema="legacy")))

    def test_forced_and_targeted_always_decide(self):
        self.assertEqual(["forced"], check(forced=True))
        self.assertEqual(["targeted"], check(targeted=True))

    def test_tracker_ignores_holding_only_triggers(self):
        cards = changed(("fundamentals", "thesis"), None)
        cards["position"]["qty"] = 99.0
        self.assertEqual([], check(cards, book="tracker", book_fp="other"))

    def test_carry_days_zero_disables_carrying(self):
        self.assertEqual(["stale"], check(carry_days=0))


if __name__ == "__main__":
    unittest.main()
