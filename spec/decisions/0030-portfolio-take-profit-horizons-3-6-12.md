# 0030 — Portfolio: take-profit sells and horizons `3m / 6m / 12m`

Date: 2026-10-05

**Revises `0013`** (adds a third sell reason), **`0014`** (holdings horizons) and the guidance
set by **`0018`**. Tracker horizons (`0020`) are unchanged.

## Change

- **Third sell reason: `take_profit`.** Jev's portfolio choice set gains `sell_take_profit`,
  parsed by `domain/doctrine.py` into `("sell", "take_profit")`. It means: the position is at a
  gain and the remaining upside over this horizon no longer justifies keeping the gain at
  risk — the run is stretched, momentum is fading, the catalyst is priced in, or the
  operator's target is reached. It is a **full** sell; there is no partial trim.
- **Price evidence may now support a sell — at a gain only.** The size of the unrealised
  gain, a stretched run (RSI, distance above moving averages) and fading momentum are
  legitimate take-profit evidence. They remain **never** a reason to sell at a loss.
- **No threshold in code.** In keeping with `0018`, nothing gates or forces the choice. The
  operator states take-profit targets in the portfolio profile; Claude and Jev weigh them.
- **Holdings horizons become `3m | 6m | 12m`** (`HORIZONS_BY_BOOK["portfolio"]`). The
  `horizon` CHECK already allows `3m`; `24m` stays legal so historical rows survive.
- **Loss label judged at 12m.** `RECOVERY_HORIZON = "12m"`; the informational label becomes
  `no_recovery_12m`. `no_recovery_24m` stays legal for pre-0030 rows.
- **Schema:** the `recommendations` `reason` CHECK gains `take_profit`, and `loss_gate` gains
  `no_recovery_12m` (table rebuild in `Database::ensureTakeProfitSchema()`).

## Why

Operator, after the system held U at +15.8% with a 6-month run of +108%: *"on the portfolio is
ok to start looking at it in 3, 6, 12 months. the point with portfolio, is to look for
opportunities to take profits."*

`0013` allowed only `thesis_broken` and `better_use` and told the models price action is never
a reason to sell — so a winner could not be sold however far it had run, and the 6m/12m/24m
horizons framed every holding as a multi-year thesis question. Taking profits is now the
portfolio's main job, so it needs its own reason, and shorter horizons that match it.

## Consequences

- The first run after deploy re-decides every holding: the materiality gate's horizon check
  rejects carrying old 6m/12m/24m rows into a 3m/6m/12m set.
- A take-profit sell is a `sell`, so it raises an alert like any other (`0017`/`0018`).
- Each portfolio question carries one more short criteria label (~6k Jev chars on the worst-case
  budget fixture; `JEV_RUN_CEILING` raised to 150k).

## Spec touchpoints

- `spec/domain.md` — sell reasons, horizons per book, loss label
- `spec/behaviour/agent-advisory.md`
- `spec/tests.md`
- `spec/decisions/0013-sell-doctrine.md`, `0014-horizons-6-12-24.md`, `0018-simplify-trust-agents.md`
