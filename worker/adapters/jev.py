from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import doctrine

# 0027: lenses follow the ingestion layers. The 0013 `thesis` lens folded into
# `fundamentals` (the business case); `historical` is the long-run record. `combined`
# runs last and sees the other four verdicts.
LENSES = ("historical", "fundamentals", "technicals", "news", "combined")

# 0022: tracked names receive instrument-linked news and the same lenses.
TRACKER_LENSES = LENSES

PORTFOLIO_CRITERIA: dict[str, str] = {
    "buy_thesis_intact_underweight": (
        "Thesis holds and the position is smaller than conviction warrants — add"
    ),
    "buy_new_conviction": "New evidence strengthens the case — add exposure",
    "sell_thesis_broken": (
        "The recorded reason for owning this no longer holds; a named falsifier has "
        "tripped on the facts. Not merely that the price fell"
    ),
    "sell_better_use": (
        "This capital has a specific better use in a named alternative, and the edge "
        "exceeds the switching cost"
    ),
    "hold": "Thesis intact; maintain current size without urgency",
    "watch": "Insufficient evidence to act; monitor closely",
}

# The tracker's mirror: the operator owns none of this, so the question is whether to start
# a position, not what to do with one.
TRACKER_CRITERIA: dict[str, str] = {
    "buy_now": (
        "The case is strong and the current price is an acceptable entry — start a position now"
    ),
    "wait_better_entry": (
        "The case holds but this is not the price to pay for it — worth owning lower, or after "
        "a specific event"
    ),
    "keep_watching": "The case is unproven either way; keep it on the tracker and keep looking",
    "drop_lost_interest": (
        "The reason for tracking this no longer holds and is unlikely to return — stop "
        "spending attention on it"
    ),
}


# 0027: questions carry only these short labels; the full definitions above travel once
# per call in `state.guidance`. Same keys, so `doctrine.split_choice` is unaffected.
PORTFOLIO_CRITERIA_SHORT: dict[str, str] = {
    "buy_thesis_intact_underweight": "add: thesis holds, underweight",
    "buy_new_conviction": "add: new evidence",
    "sell_thesis_broken": "sell: falsifier tripped",
    "sell_better_use": "sell: better named use",
    "hold": "keep size",
    "watch": "monitor",
}

TRACKER_CRITERIA_SHORT: dict[str, str] = {
    "buy_now": "start now",
    "wait_better_entry": "wait for entry",
    "keep_watching": "keep looking",
    "drop_lost_interest": "stop tracking",
}


def _lens_focus(lens: str, book: str) -> str:
    if book == "tracker":
        return {
            "historical": (
                "Use the long-run record only (multi-year returns, drawdowns, volatility, "
                "52-week range, excess return vs the regional proxy) and Claude's historical "
                "note. The question is whether today's price is a reasonable entry against "
                "this name's own history."
            ),
            # There is no thesis of record for a name that was never bought, so the
            # fundamentals lens asks the entry question — the business case for owning it.
            "fundamentals": (
                "Use the fundamentals, the researcher's entry case and what would have to be "
                "true to buy — ignore price level and technicals. The question is whether the "
                "reason to want this holds up."
            ),
            "technicals": (
                "Use RSI/SMA/returns and Claude's technical note only — ignore the entry case. "
                "The question is whether this is a sensible price and moment to start."
            ),
            "news": (
                "Use the recent headlines and Claude's news note only — ignore price levels and "
                "technicals. The question is whether the news changes the case for starting."
            ),
            "combined": (
                "Weigh the four lens verdicts and layer notes together with the operator's cash "
                "reserve and what they already own, and say whether to start a position."
            ),
        }.get(lens, "Use all available evidence.")

    return {
        "historical": (
            "Use the long-run record only (multi-year returns, drawdowns, volatility, 52-week "
            "range, excess return vs the regional proxy) and Claude's historical note. It is "
            "context for the thesis — a poor record is never by itself a reason to sell."
        ),
        "fundamentals": (
            "Use the fundamentals, the recorded thesis, its falsifiers, and the researcher's "
            "thesis_status and evidence only — ignore price level, P&L and technicals. The "
            "question is whether the reason for owning this still holds."
        ),
        "news": "Use recent headlines and Claude's news note only — ignore price levels and technicals.",
        "technicals": "Use RSI/SMA/returns and Claude's technical note only — ignore news headlines.",
        "combined": (
            "Weigh the four lens verdicts and layer notes together with full book context "
            "(sizes, costs, held_days, concentration, cash) for the best overall advisory action."
        ),
    }.get(lens, "Use all available evidence.")


def _book_guidance(book: str) -> str:
    if book == "tracker":
        return (
            "The operator does not own this — it is on their tracker as a name of interest. "
            "Buying is the only action that costs them anything, so it needs a reason that "
            "would survive being wrong. A high price alone is a reason to wait, not to drop; "
            "drop only when the reason for tracking it has actually gone."
        )
    return (
        "The operator's preference: a sell should be because the investment thesis is broken, "
        "or because the capital has a specific better named use — not because the price fell. "
        "Price action, momentum, drawdown depth and concentration are evidence you may reason "
        "from, but weigh them against the thesis rather than acting on them directly. If you "
        "can't point to a broken thesis or a better use, prefer hold or watch."
    )


class JevError(RuntimeError):
    pass


class JevAdapter:
    """TypeSafe System One (Jev) via HTTPS — typed Choice/Noul decisions."""

    def __init__(self) -> None:
        self.api_key = (os.environ.get("TYPESAFE_API_KEY") or "").strip()
        base = (os.environ.get("TYPESAFE_BASE_URL") or "https://api.typesafe.ai").rstrip("/")
        self.url = f"{base}/v1/systemone"
        self.model = os.environ.get("TYPESAFE_MODEL", "jev-latest")
        self.timeout = int(os.environ.get("TYPESAFE_TIMEOUT_SECONDS", "60"))
        self.last_request_chars = 0

    def enabled(self) -> bool:
        return bool(self.api_key)

    def status(self) -> dict:
        return {
            "enabled": self.enabled(),
            "model": self.model,
            "url": self.url,
        }

    def system_one(self, state: str | dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise JevError("TYPESAFE_API_KEY is not set")

        payload = {
            "model": self.model,
            "state": state,
            "questions": questions,
        }
        body = json.dumps(payload).encode("utf-8")
        req = Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise JevError(f"Jev HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise JevError(f"Jev unreachable: {exc.reason}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise JevError("Jev returned non-JSON") from exc
        if not isinstance(data, dict):
            raise JevError("Jev returned unexpected payload")
        return data

    def guidance(
        self,
        *,
        lens: str,
        books: set[str],
        mandates: dict[str, str] | None = None,
        extra_instructions: str | None = None,
    ) -> dict[str, Any]:
        """
        Everything every question in this call shares, stated once (0027). The per-question
        text used to repeat all of this 168 times a run. A question names `SYM horizon (book)`;
        its book's entry here says how to answer it.
        """
        mandates = mandates or {}
        out: dict[str, Any] = {"lens": lens, "by_book": {}}
        for book in sorted(books):
            out["by_book"][book] = {
                "mandate": mandates.get(book) or doctrine.DEFAULT_MANDATE,
                "lens_focus": _lens_focus(lens, book),
                "book_guidance": _book_guidance(book),
                "choices": TRACKER_CRITERIA if book == "tracker" else PORTFOLIO_CRITERIA,
            }
        if extra_instructions:
            out["scenario_focus"] = extra_instructions
        return out

    def choose_actions(
        self,
        state: dict[str, Any],
        instrument_keys: list[tuple[str, str, int, str]],
        *,
        lens: str = "combined",
        extra_instructions: str | None = None,
        mandates: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        instrument_keys: list of (question_id, symbol, instrument_id, book)

        Both books ride in one call (0019). The mandate, lens focus, book guidance and full
        choice definitions go once into `state.guidance`; each question names only its
        subject, horizon and book, with short labels on the same composite keys (0027).

        Returns raw System One response plus a flattened map question_id -> answer.
        """
        books = {book for _qid, _sym, _iid, book in instrument_keys}
        state = dict(state)
        state["guidance"] = self.guidance(
            lens=lens, books=books, mandates=mandates, extra_instructions=extra_instructions
        )

        questions: dict[str, Any] = {}
        for qid, symbol, _iid, book in instrument_keys:
            horizon = qid.rsplit("_", 1)[-1]
            questions[qid] = {
                "type": "choice",
                "instructions": f"{symbol} {horizon} ({book})",
                # The choice set carries the reason, so a bare "sell" is unrepresentable.
                "criteria": TRACKER_CRITERIA_SHORT if book == "tracker" else PORTFOLIO_CRITERIA_SHORT,
            }

        self.last_request_chars = len(json.dumps({"state": state, "questions": questions}))
        raw = self.system_one(state, questions)
        answers = raw.get("answers") or {}
        flattened: dict[str, Any] = {}
        if isinstance(answers, dict):
            for qid, ans in answers.items():
                flattened[qid] = ans
        return {"raw": raw, "answers": flattened, "lens": lens, "request_chars": self.last_request_chars}
