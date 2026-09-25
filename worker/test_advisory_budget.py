"""
Token budget guard (0027): a 14-subject book stays inside fixed Claude and Jev payload
ceilings, shared guidance travels once per call, and each lens sees only its own layer.

Run: docker compose exec worker python test_advisory_budget.py
"""

from __future__ import annotations

import json
import unittest
from datetime import date, timedelta

import digest
import doctrine
from advisory import AdvisoryService, LENSES

# Worst case: this fixture hits every cap (thesis, falsifiers, four news items with
# snippets, long profiles) and every subject is being decided. Before 0027 a lighter real
# book of this size measured ~350k Jev chars, and its ~25k-char Claude prompt also paid
# ~27k tokens of CLI system prompt and tool definitions. Most of what is left in Jev is
# the per-question criteria labels, which the System One question shape requires.
JEV_RUN_CEILING = 140_000
CLAUDE_PROMPT_CEILING = 42_000


def subject(i: int, book: str) -> dict:
    end = date(2026, 9, 25)
    history = [((end - timedelta(days=d)).isoformat(), 100 + (i + d) % 17) for d in range(1800, -1, -7)]
    s = {
        "instrument_id": i,
        "symbol": f"SYM{i}.XX",
        "name": f"Company number {i} with a long legal name S.A.",
        "book": book,
        "kind": "equity",
        "region": "eu",
        "currency": "EUR",
        "quote": {"price": 123.456789},
        "quantity": 12.0,
        "avg_cost": 98.7654321,
        "cost_display": 1185.18518,
        "market_value_display": 1481.4814,
        "pnl_pct": 24.99999,
        "weight_pct": 7.123456,
        "weight_cost_pct": 6.54321,
        "held_days": 400,
        "open_lot_count": 2,
        "added_at": "2026-01-01T00:00:00+00:00",
        "fundamentals": {
            "payload": {k: 0.123456 for k in digest.EQUITY_FUNDAMENTALS} | {
                "market_cap": 5e10, "sector": "Industrials", "description": "d" * 3000,
            },
            "completeness_state": "complete",
            "missing_fields": [],
            "as_of": "2026-09-20T00:00:00Z",
        },
        "technicals": {
            "as_of_bar": "2026-09-24", "rsi_14": 55.55, "vs_sma20_pct": 1.234, "vs_sma50_pct": -2.345,
            "return_1m_pct": 3.21, "return_3m_pct": -4.56, "return_6m_pct": 7.89,
        },
        "news": [
            {"title": f"Headline {n} about company {i} and its sector outlook this quarter",
             "snippet": "s" * 400, "source": "example.com", "published_at": "2026-09-2{n}"}
            for n in range(6)
        ],
        "thesis": {"text": "t" * 600, "falsifiers": ["f" * 200] * 4, "version": 2} if book == "portfolio" else None,
    }
    s["cards"] = digest.build_cards(s, history=history, benchmark=history, benchmark_symbol="EXSA.DE")
    return s


def context() -> dict:
    holdings = [subject(i, "portfolio") for i in range(1, 9)]
    tracked = [subject(i, "tracker") for i in range(9, 15)]
    profiles = {"investor": "i" * 800, "portfolio": "p" * 600}
    return {
        "display_currency": "EUR",
        "portfolio_market_value_display": 12000.0,
        "cash_display": 5000.0,
        "realized_gains_ytd_display": 0.0,
        "calendar_year": 2026,
        "profiles": profiles,
        "mandates": {b: doctrine.compose_mandate(profiles, b) for b in doctrine.BOOKS},
        "holdings": holdings,
        "tracked": tracked,
        "layer_notes": {
            s["symbol"]: {lens: "n" * 150 for lens in ("historical", "fundamentals", "technicals", "news")}
            for s in holdings + tracked
        },
    }


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.svc = AdvisoryService(":memory:")
        self.calls: list[tuple[dict, dict]] = []

        def fake_system_one(state, questions):
            self.calls.append((state, questions))
            return {"answers": {qid: {"choice": "hold"} for qid in questions}}

        self.svc.jev.system_one = fake_system_one

    def run_lenses(self, ctx: dict) -> int:
        answers: dict = {}
        total = 0
        for lens in LENSES:
            state = self.svc._lens_state(ctx, {}, lens, lens_answers=answers)
            result = self.svc.jev.choose_actions(
                state, self.svc._instrument_keys(ctx, lens=lens), lens=lens,
                mandates=self.svc._mandates(ctx),
            )
            answers[lens] = result["answers"]
            total += result["request_chars"]
        return total

    def test_jev_run_under_ceiling(self):
        total = self.run_lenses(context())
        self.assertLess(total, JEV_RUN_CEILING, f"Jev run grew to {total} chars")

    def test_guidance_once_per_call_and_tiny_questions(self):
        ctx = context()
        self.run_lenses(ctx)
        self.assertEqual(len(LENSES), len(self.calls))
        for state, questions in self.calls:
            payload = json.dumps({"state": state, "questions": questions})
            # The investor profile rides in both books' mandates; the portfolio profile in one.
            self.assertEqual(2, payload.count("i" * 800))
            self.assertEqual(1, payload.count("p" * 600))
            for q in questions.values():
                self.assertLessEqual(len(q["instructions"]), 40)

    def test_each_evidence_lens_sees_only_its_layer(self):
        ctx = context()
        foreign = {
            "historical": ("fund", "tech", "news", "pos"),
            "fundamentals": ("hist", "tech", "news", "pos"),
            "technicals": ("hist", "fund", "news", "pos"),
            "news": ("hist", "fund", "tech", "pos"),
            "combined": ("hist", "fund", "tech", "news"),
        }
        for lens, keys in foreign.items():
            state = self.svc._lens_state(ctx, {}, lens)
            for row in state["holdings"] + state["tracked"]:
                for key in keys:
                    self.assertNotIn(key, row, f"{lens} lens leaked {key}")
            self.assertEqual(lens == "combined", "cash_display" in state)

    def test_combined_sees_lens_verdicts(self):
        ctx = context()
        self.run_lenses(ctx)
        combined_state = self.calls[-1][0]
        row = combined_state["holdings"][0]
        self.assertEqual({"historical", "fundamentals", "technicals", "news"}, set(row["lens_verdicts"]))
        self.assertEqual("6m:hold, 12m:hold, 24m:hold", row["lens_verdicts"]["news"])

    def test_claude_prompt_under_ceiling(self):
        prompts: list[str] = []

        def fake_analyze(prompt, *, system_prompt=None, json_schema=None):
            prompts.append(prompt)
            self.assertIsNotNone(system_prompt)
            self.assertIsNotNone(json_schema)
            return "{}"

        self.svc.claude.analyze = fake_analyze
        self.svc._claude_research(context(), {}, [])
        self.assertLess(len(prompts[0]), CLAUDE_PROMPT_CEILING, f"Claude prompt grew to {len(prompts[0])}")
        self.assertNotIn("d" * 100, prompts[0])  # fundamentals description never sent


if __name__ == "__main__":
    unittest.main()
