from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import doctrine

# 0013: `prices` replaced by `thesis`. The prices lens judged on quote and P&L alone,
# which is exactly the reasoning the operator rejects as grounds for a sell. Swapping
# rather than adding keeps the call count (and token cost) unchanged.
LENSES = ("thesis", "news", "technicals", "combined")

# 0019: a tracked name has no news ingested (the Marketaux quota is spent on the book the
# operator actually owns), so it is left out of the news lens rather than asked a question
# with no evidence behind it. `lensLine()` in the UI already skips a missing lens.
TRACKER_LENSES = ("thesis", "technicals", "combined")

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


def _lens_focus(lens: str, book: str) -> str:
    if book == "tracker":
        return {
            # There is no thesis of record for a name that was never bought, so the `thesis`
            # lens asks the entry question instead — same slot, mirrored question.
            "thesis": (
                "Use the researcher's entry case and what would have to be true to buy — "
                "ignore price level and technicals. The question is whether the reason to "
                "want this holds up."
            ),
            "technicals": (
                "Use RSI/SMA/returns and Claude technical notes only — ignore the entry case. "
                "The question is whether this is a sensible price and moment to start."
            ),
            "combined": (
                "Weigh the entry case and the technicals together with the operator's cash "
                "reserve and what they already own, and say whether to start a position."
            ),
        }.get(lens, "Use all available evidence.")

    return {
        "thesis": (
            "Use the recorded investment thesis, its falsifiers, and the researcher's "
            "thesis_status and evidence only — ignore price level, P&L and technicals. "
            "The question is whether the reason for owning this still holds."
        ),
        "news": "Use recent headlines/snippets and Claude news notes only — ignore price levels and technicals.",
        "technicals": "Use RSI/SMA/returns and Claude technical notes only — ignore news headlines.",
        "combined": (
            "Weigh thesis, news, and technicals together with full book context "
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

    def choose_actions(
        self,
        state: str | dict[str, Any],
        instrument_keys: list[tuple[str, str, int, str]],
        *,
        lens: str = "combined",
        extra_instructions: str | None = None,
        mandates: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        instrument_keys: list of (question_id, symbol, instrument_id, book)

        Both books ride in one call (0019). System One takes per-question criteria, so a
        tracked name can be offered a different choice set and a different mandate without
        costing a second round trip.

        Returns raw System One response plus a flattened map question_id -> answer.
        """
        mandates = mandates or {}

        questions: dict[str, Any] = {}
        for qid, symbol, _iid, book in instrument_keys:
            horizon = qid.rsplit("_", 1)[-1]
            mandate = mandates.get(book) or doctrine.DEFAULT_MANDATE
            instructions = (
                f"MANDATE: {mandate} "
                f"Lens={lens}. {_lens_focus(lens, book)} "
                f"For listed instrument {symbol}, choose one advisory action over a {horizon} horizon. "
                f"{_book_guidance(book)}"
            )
            if extra_instructions:
                instructions = f"{instructions} Scenario focus: {extra_instructions}"
            questions[qid] = {
                "type": "choice",
                "instructions": instructions,
                # The choice set carries the reason, so a bare "sell" is unrepresentable.
                "criteria": TRACKER_CRITERIA if book == "tracker" else PORTFOLIO_CRITERIA,
            }

        raw = self.system_one(state, questions)
        answers = raw.get("answers") or {}
        flattened: dict[str, Any] = {}
        if isinstance(answers, dict):
            for qid, ans in answers.items():
                flattened[qid] = ans
        return {"raw": raw, "answers": flattened, "lens": lens}
