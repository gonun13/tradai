# 0020 — Tracker horizons `1m / 3m / 6m`

Date: 2026-09-21

**Revises `0014`** for the tracker book only. Holdings are unaffected: they keep `6m / 12m / 24m`.

## Change

- The tracker (`0019`) is judged over **`1m | 3m | 6m`**, not the holdings' `6m | 12m | 24m`.
- `HORIZONS` in `worker/services/advisory.py` becomes book-scoped:
  `HORIZONS_BY_BOOK = {"portfolio": ("6m", "12m", "24m"), "tracker": ("1m", "3m", "6m")}`.
  Every per-subject horizon loop reads the set for `h["book"]` instead of one global tuple.
- The `horizon` CHECK constraint is **widened again** to
  `IN ('1m', '3m', '6m', '12m', '24m')`, same approach as `0014`: old values stay legal so
  existing rows survive, nothing is removed.
- The tracker table's gain columns (`1m`, `3m` — `0019`) get a third, `6m`, once
  `domain/technicals.py` computes it, so the columns the operator looks at line up with the horizons
  Jev is actually deciding over.

## Why

`0014` picked `6m / 12m / 24m` for a single undifferentiated book, and `24m` earned its place
specifically to back the `no_recovery_24m` loss gate (`0013`). The tracker has no position and
no loss gate (`spec/behaviour/tracker.md`: "no loss to be at, no proceeds to redeploy") — so the
horizon that exists to serve that gate is dead weight on tracker rows, and the two long horizons
above it (`12m`, `24m`) are answering a multi-year holding question a name you don't yet own
was never asked.

The tracker's own verbs — `buy_now` / `wait_better_entry` / `keep_watching` /
`drop_lost_interest` — are an entry-timing question, not a multi-year thesis question. `1m / 3m
/ 6m` matches that: short enough to be about *when to buy*, and `6m` still gives a checkpoint
that lines up with the holdings' shortest horizon so a name moving from tracker to portfolio
isn't a horizon-vocabulary jump.

Operator: *"on the tracker side i'm looking for 1 month, 3 months, 6 months gains not 6, 12,
24."*

## Spec touchpoints

- `spec/domain.md` — horizons are now per-book
- `spec/behaviour/tracker.md`, `spec/behaviour/agent-advisory.md`
- `spec/decisions/0014-horizons-6-12-24.md` (revised for the tracker book only)
- `spec/decisions/0019-tracker-as-second-book.md` (tracker table gain columns)
