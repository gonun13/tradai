# 0012 — Investor mandate

Date: 2026-09-20

## Change

State the operator's objective function. Until now no spec or prompt said what "good" means, so the advisory agents optimised nothing in particular.

## Decision

The mandate, as affirmed by the operator:

- **Scoreboard:** compound capital long-term, judged over a **5-year** window. Not benchmark-relative, not income, not capital preservation.
- **Typical holding period:** **6–18 months**. Rotation is expected; turnover is not itself a cost to be minimised, but neither is it a goal.
- **Cash:** the operator holds a deliberate **cash reserve** outside the book. Agents see it and may allocate from it. A buy does not require a sell when cash is available.
- **Tax residence:** **Portugal**. Realising a loss is disfavoured; see `0013` for the two conditions under which it is acceptable.
- **Risk posture:** drawdown alone is not a sell signal and concentration alone is not a sell signal. See `0013`.

This mandate is passed to Claude and to Jev on every run. It is not advisory flavour text — `0013` makes parts of it executable.

## Why

`worker/advisory.py` told Claude only "Be concise", and `worker/adapters/jev.py` asked for "the best overall advisory action" without defining best. An unanchored model shown a negative P&L number recommends selling. The operator observed exactly that and it is the proximate cause of this decision.

## Spec touchpoints

- `spec/PROJECT.md`, `spec/domain.md`
- `spec/behaviour/agent-advisory.md`
- `spec/decisions/0013-sell-doctrine.md`, `0014`, `0015`, `0016`
