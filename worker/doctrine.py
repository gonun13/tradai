"""
Sell-doctrine labels (spec/decisions/0013, revised — see 0018-simplify-trust-agents.md).

Claude and Jev decide the action. Nothing here overrides them. This module only:

1. Parses Jev's composite choice (e.g. ``sell_thesis_broken``) into (action, reason), so a
   sell always carries a reason without a separate field the model could omit — that's free
   structure, not a gate.
2. Computes an informational loss-gate label (whether a loss-making sell lines up with a
   same-year offset or a "no recovery" call) for the log to display. It never changes what
   gets persisted.

The actual guidance — the mandate, and the two reasons the operator accepts for a sell — is
given to Claude and Jev directly in their prompts. They apply it; this module just labels
the result for the operator to see.
"""

from __future__ import annotations

from typing import Any

# Composite choice -> (action, reason). Gives every sell a reason for free.
CHOICE_TO_ACTION: dict[str, tuple[str, str]] = {
    "buy_thesis_intact_underweight": ("buy", "thesis_intact_underweight"),
    "buy_new_conviction": ("buy", "new_conviction"),
    "sell_thesis_broken": ("sell", "thesis_broken"),
    "sell_better_use": ("sell", "better_use"),
    "hold": ("hold", "thesis_intact"),
    "watch": ("watch", "insufficient_evidence"),
}

# 0019: a name on the tracker isn't owned, so "hold" and every "sell" are unanswerable.
# Its own choice set keeps the same trick — the verb carries the reason.
TRACKER_CHOICE_TO_ACTION: dict[str, tuple[str, str]] = {
    "buy_now": ("buy", "entry_now"),
    "wait_better_entry": ("watch", "await_better_entry"),
    "keep_watching": ("watch", "insufficient_evidence"),
    "drop_lost_interest": ("drop", "lost_interest"),
}

BOOKS = ("portfolio", "tracker")

RECOVERY_HORIZON = "24m"

# 0019: the single mandate splits in two. Both are defaults, used only until the operator
# writes their own on the Setup page.

# Who the operator is and what makes a name worth buying. Applies to the tracker, and
# travels as global context on every call — a holding is judged by someone, after all.
DEFAULT_INVESTOR_PROFILE = (
    "Compound capital long-term, judged over 5 years. Buy a business, not a ticker: a "
    "durable reason it will be worth more in five years, bought at a price that does not "
    "already assume it. Typical holding period 6-18 months. Prefer a name understood well "
    "enough to say what would prove the case wrong."
)

# The rules for names already owned: sizing, trimming, when to sell, tax.
DEFAULT_PORTFOLIO_PROFILE = (
    "A cash reserve is held back and may fund buys — a buy does not require a sell. "
    "Selling at a loss is disfavoured unless it offsets a gain realised this calendar year, "
    "or the position has no plausible recovery within 24 months. Drawdown depth and "
    "concentration alone are not sell signals."
)

# Pre-0019 name. The single mandate was portfolio-management guidance in practice, so this
# is the half it became.
DEFAULT_MANDATE = DEFAULT_PORTFOLIO_PROFILE


def choice_map(book: str = "portfolio") -> dict[str, tuple[str, str]]:
    return TRACKER_CHOICE_TO_ACTION if book == "tracker" else CHOICE_TO_ACTION


def split_choice(choice: Any, book: str = "portfolio") -> tuple[str, str] | None:
    """Map a composite Jev choice onto (action, reason). None when unrecognised."""
    if not isinstance(choice, str):
        return None
    return choice_map(book).get(choice.strip().lower())


def resolved_portfolio_profile(profiles: dict[str, Any] | None) -> str:
    """The holdings-only profile text, including its unwritten fallback (0019)."""
    return ((profiles or {}).get("portfolio") or "").strip() or DEFAULT_PORTFOLIO_PROFILE


def compose_mandate(profiles: dict[str, Any] | None, book: str = "portfolio") -> str:
    """
    The text a model is actually given for this book (0019). The investor profile always
    travels — it says who is asking — and the portfolio profile is added only when the
    subject is something the operator owns.
    """
    profiles = profiles or {}
    investor = (profiles.get("investor") or "").strip() or DEFAULT_INVESTOR_PROFILE
    if book == "tracker":
        return investor
    portfolio = resolved_portfolio_profile(profiles)
    return f"{investor} {portfolio}"


def label_loss_gate(
    *,
    pnl_pct: float | None,
    realized_gains_ytd_eur: float | None,
    horizon_choices: dict[str, str],
) -> str | None:
    """
    Informational label for a sell at the current P&L. Purely descriptive — shown in the
    log so the operator can see how a sell lines up with their own tax preference, not used
    to allow or block anything.
    """
    if pnl_pct is None or pnl_pct >= 0:
        return "not_at_loss" if pnl_pct is not None else None
    if (realized_gains_ytd_eur or 0.0) > 0:
        return "offset_same_year"
    if horizon_choices.get(RECOVERY_HORIZON) == "sell":
        return "no_recovery_24m"
    return None
