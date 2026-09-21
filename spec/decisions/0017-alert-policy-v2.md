# 0017 — Alert policy v2

Date: 2026-09-20

**Supersedes `0010-alert-policy.md`.**

## Change

Replace "any buy or sell raises an alert, no confidence threshold" with a policy aware of the sell doctrine (`0013`).

## Decision

Raise an `Alert` when:

- A **sell passed the doctrine gate** — severity `sell`.
- A holding's `thesis_status` is reported **`broken`** — severity `thesis`, anchored to that instrument's
  `12m` recommendation row so it reuses the Alert→Recommendation relation. Fires independently of the
  trade verdict, including when the sell that would have followed was suppressed, because a broken
  thesis is the thing the operator actually needs told.
- A **funded** buy (cash available or paired with a passing sell) clears the confidence floor — severity `buy`.

Never raise for:

- A **suppressed** recommendation. The gate rejected it; surfacing it would reintroduce exactly the noise this work removes.
- `hold` or `watch`.
- An unfunded buy.

A **thesis awaiting approval** is deliberately *not* an Alert. `domain.md` defines an Alert as deriving
from a Recommendation, and a pending draft derives from nothing; it surfaces instead as a count on
`GET /theses` (`coverage[].pending_drafts`) for the UI to badge. This matters because the doctrine fails
closed — a forgotten draft silently disables `thesis_broken` for that holding.

A **confidence floor** now applies (env-tunable). `0010` explicitly declined one on the grounds that "buy/sell alone is enough"; with a doctrine gate upstream, the surviving recommendations are fewer and a floor no longer risks silencing the panel.

## Why

`0010` was written when any action was informative. Under `0013` most sells are suppressed, so alerting on raw action would either fire on rejected reasoning or go quiet. Alerting on *gate-passing* actions and on *thesis breaks* tracks what the operator asked to be told.

## Spec touchpoints

- `spec/domain.md`, `spec/behaviour/agent-advisory.md`
- Supersedes `spec/decisions/0010-alert-policy.md`
