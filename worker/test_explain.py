"""
Claude explains the decision (0028): the prompt carries the combined choice, the lens
verdicts and the market backdrop; parsing is tolerant; a failure never costs the run.

Run: docker compose exec worker python test_explain.py
"""

from __future__ import annotations

import json
import unittest

from adapters.claude_cli import ClaudeCliError
from advisory import EXPLAIN_SCHEMA, EXPLAIN_SYSTEM_PROMPT, AdvisoryService, EVIDENCE_LENSES, horizons_for
from test_advisory_budget import context


def answers_for(ctx, choice_by_book):
    out = {}
    for s_ in ctx["holdings"] + ctx["tracked"]:
        for hz in horizons_for(s_["book"]):
            out[f"{s_['instrument_id']}_{hz}"] = {
                "choice": choice_by_book[s_["book"]],
                "confidence": 0.76,
                "probabilities": {choice_by_book[s_["book"]]: 0.8, "hold": 0.11, "sell_better_use": 0.0},
            }
    return out


class ExplainTests(unittest.TestCase):
    def setUp(self):
        self.svc = AdvisoryService(":memory:")
        self.ctx = context()
        self.ctx["market"] = {"eu": {"bench": "EXSA.DE", "ret_1m_pct": -2.1, "ret_1y_pct": 8.4}}
        self.ctx["synthesis"] = "Book-wide synthesis."
        self.lens_answers = {
            lens: answers_for(self.ctx, {"portfolio": "hold", "tracker": "keep_watching"})
            for lens in EVIDENCE_LENSES
        }
        self.combined = answers_for(self.ctx, {"portfolio": "watch", "tracker": "wait_better_entry"})

    def test_prompt_carries_decision_verdicts_and_market(self):
        prompt = self.svc._explain_prompt(self.ctx, {}, self.lens_answers, self.combined)
        subjects = json.loads(prompt.split("SUBJECTS:\n", 1)[1])
        first = subjects[0]
        self.assertEqual(
            {"action": "watch", "reason": "insufficient_evidence", "confidence": 0.76,
             "top": {"watch": 0.8, "hold": 0.11}},
            first["decision"]["12m"],
        )
        self.assertEqual("6m:hold, 12m:hold, 24m:hold", first["lenses"]["news"])
        self.assertIn("EXSA.DE", prompt)
        self.assertIn("HEADLINES:", prompt)
        self.assertIn("Book-wide synthesis.", prompt)
        tracked = next(s for s in subjects if s["book"] == "tracker")
        self.assertEqual("await_better_entry", tracked["decision"]["3m"]["reason"])
        # 0026: history is summarised, never shipped as points.
        self.assertNotIn("adjusted_close", prompt)
        self.assertNotIn("2021-", prompt)

    def test_uses_bare_structured_call_and_parses_tolerantly(self):
        seen = {}

        def fake_analyze(prompt, *, system_prompt=None, json_schema=None):
            seen["system"], seen["schema"] = system_prompt, json_schema
            return json.dumps({
                "market_read": "Europe soft on the month.",
                "by_symbol": {
                    "SYM1.XX": {"explanation": "Held because the thesis is intact.", "tension": "Technicals lean weaker."},
                    "sym2.xx": {"explanation": "Lower-cased key still matches.", "tension": None},
                    "SYM3.XX": {"explanation": "   ", "tension": None},
                    "NOPE": {"explanation": "Unknown symbols are ignored.", "tension": None},
                },
            })

        self.svc.claude.analyze = fake_analyze
        log: list[str] = []
        out = self.svc._claude_explain(self.ctx, {}, self.lens_answers, self.combined, log)
        self.assertIs(EXPLAIN_SYSTEM_PROMPT, seen["system"])
        self.assertIs(EXPLAIN_SCHEMA, seen["schema"])
        self.assertEqual("Europe soft on the month.", out["market_read"])
        self.assertEqual({"SYM1.XX", "SYM2.XX"}, set(out["by_symbol"]))
        self.assertEqual("Technicals lean weaker.", out["by_symbol"]["SYM1.XX"]["tension"])
        self.assertIsNone(out["by_symbol"]["SYM2.XX"]["tension"])
        self.assertTrue(any(line.startswith("claude: explanations=2") for line in log))

    def test_failure_is_logged_not_raised(self):
        def boom(prompt, **_kwargs):
            raise ClaudeCliError("rate limited")

        self.svc.claude.analyze = boom
        log: list[str] = []
        out = self.svc._claude_explain(self.ctx, {}, self.lens_answers, self.combined, log)
        self.assertEqual({"market_read": "", "by_symbol": {}, "usage": None}, out)
        self.assertIn("claude explain skipped: rate limited", log)

    def test_garbage_output_yields_nothing(self):
        self.assertEqual(
            {"market_read": "", "by_symbol": {}},
            self.svc._parse_explanations("not json", self.ctx),
        )


if __name__ == "__main__":
    unittest.main()
