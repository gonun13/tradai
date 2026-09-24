"""
Doctrine module tests (0013, revised). No gating anymore — this only checks the two pure
helpers: parsing Jev's composite choice, and the informational loss-gate label.

Run: docker compose exec worker python test_doctrine.py
"""

from __future__ import annotations

import doctrine

print("composite choice parsing")
assert doctrine.split_choice("sell_thesis_broken") == ("sell", "thesis_broken")
assert doctrine.split_choice("sell_better_use") == ("sell", "better_use")
assert doctrine.split_choice("buy_new_conviction") == ("buy", "new_conviction")
assert doctrine.split_choice("hold") == ("hold", "thesis_intact")
assert doctrine.split_choice("watch") == ("watch", "insufficient_evidence")
assert doctrine.split_choice("sell") is None  # bare action carries no reason
assert doctrine.split_choice(None) is None
assert doctrine.split_choice(123) is None
print("  ok")

print("\ntracker choice parsing (0019) — a name you don't own has its own verbs")
assert doctrine.split_choice("buy_now", "tracker") == ("buy", "entry_now")
assert doctrine.split_choice("wait_better_entry", "tracker") == ("watch", "await_better_entry")
assert doctrine.split_choice("keep_watching", "tracker") == ("watch", "insufficient_evidence")
assert doctrine.split_choice("drop_lost_interest", "tracker") == ("drop", "lost_interest")
# The books don't share a vocabulary: each set is meaningless in the other's context, and
# reading one with the other's map must fail rather than silently mis-label the reason.
assert doctrine.split_choice("wait_better_entry") is None
assert doctrine.split_choice("hold", "tracker") is None
assert doctrine.split_choice("sell_thesis_broken", "tracker") is None
assert doctrine.split_choice("buy_now") is None
print("  ok")

print("\nloss-gate label — informational only, never blocks anything")
NO_GAINS = {"6m": "sell", "12m": "sell", "24m": "sell"}
SHORT_ONLY = {"6m": "sell", "12m": "sell", "24m": "hold"}

assert doctrine.label_loss_gate(pnl_pct=5.0, realized_gains_ytd_eur=0, horizon_choices=NO_GAINS) == "not_at_loss"
assert doctrine.label_loss_gate(pnl_pct=-10.0, realized_gains_ytd_eur=500, horizon_choices=NO_GAINS) == "offset_same_year"
assert doctrine.label_loss_gate(pnl_pct=-10.0, realized_gains_ytd_eur=0, horizon_choices=NO_GAINS) == "no_recovery_24m"
assert doctrine.label_loss_gate(pnl_pct=-10.0, realized_gains_ytd_eur=0, horizon_choices=SHORT_ONLY) is None
assert doctrine.label_loss_gate(pnl_pct=None, realized_gains_ytd_eur=0, horizon_choices=NO_GAINS) is None
print("  ok")

print("\nprofile defaults are non-empty fallbacks for an operator who hasn't written one yet")
assert isinstance(doctrine.DEFAULT_MANDATE, str) and len(doctrine.DEFAULT_MANDATE) > 20
assert len(doctrine.DEFAULT_INVESTOR_PROFILE) > 20
assert len(doctrine.DEFAULT_PORTFOLIO_PROFILE) > 20
print("  ok")

print("\ncompose_mandate (0019) — the investor profile travels, the portfolio profile doesn't")
profiles = {"investor": "INV", "portfolio": "PORT"}
assert doctrine.compose_mandate(profiles, "tracker") == "INV"
assert doctrine.compose_mandate(profiles, "portfolio") == "INV PORT"
assert doctrine.resolved_portfolio_profile(profiles) == "PORT"
assert doctrine.resolved_portfolio_profile({"portfolio": " PORT "}) == "PORT"
assert doctrine.resolved_portfolio_profile({"portfolio": "  "}) == doctrine.DEFAULT_PORTFOLIO_PROFILE
# An unwritten half falls back to its own default rather than leaking the other book's rules.
half = doctrine.compose_mandate({"investor": None, "portfolio": "PORT"}, "tracker")
assert half == doctrine.DEFAULT_INVESTOR_PROFILE
assert "PORT" not in half
empty = doctrine.compose_mandate({}, "portfolio")
assert doctrine.DEFAULT_INVESTOR_PROFILE in empty and doctrine.DEFAULT_PORTFOLIO_PROFILE in empty
assert doctrine.compose_mandate(None, "tracker") == doctrine.DEFAULT_INVESTOR_PROFILE
print("  ok")

print("\nALL DOCTRINE TESTS PASSED")
