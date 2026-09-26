# 0013 — Sell doctrine

Date: 2026-09-20

## Change

Define the only conditions under which the system may recommend a sell, and enforce them in code rather than in prompt text.

## Decision

### Valid sell reasons — exhaustive

1. **`thesis_broken`** — the recorded reason for owning the position no longer holds. Requires an **approved** thesis on record (`0015`). Fundamentals and facts, not price.
2. **`better_use`** — the capital has a specific, named better use. Requires a `pair_symbol`: a holding or watchlist candidate (`0016`) the proceeds fund. Never a sell into cash for its own sake.

### Explicitly rejected as sell reasons

- Price action, momentum, moving averages, RSI, trailing stops, drawdown depth.
- Risk rebalancing or concentration limits.

Technical and price evidence remain **inputs to a thesis test**. They may never be the *reason* for a sell.

### Loss gates

If the position is at an unrealised loss, an otherwise-valid sell additionally requires one of:

- **`offset_same_year`** — the realised loss offsets a gain realised in the **same calendar year** (`realized_gains_ytd_eur > 0`).
- **`no_recovery_24m`** — no plausible recovery within **24 months**, asserted with supporting evidence and meeting a confidence floor.

A position at a gain needs no loss gate (`not_at_loss`).

### Enforcement

Prompt **and** post-hoc validation. `worker/domain/doctrine.py` evaluates every canonical recommendation before persistence; anything failing the doctrine is suppressed, downgraded, and written with a `suppressed_reason`. The gate **fails closed**: absent or unparseable reason data suppresses rather than permits.

Suppressed recommendations never raise alerts (`0017`).

## Why

Operator accepts exactly two sell reasons and rejects the two the pipeline was structurally capable of producing. Both accepted reasons were previously impossible to compute — no thesis was stored, and the agent context selected only owned instruments, so no alternative could be named. Every sell shown to the operator therefore rested on rejected reasoning.

Enforcement is in code because prompt-trust is what failed: the previous instructions were followed faithfully and still produced the wrong answer, because the instruction itself was empty.

## Spec touchpoints

- `spec/domain.md` — advisory semantics
- `spec/behaviour/agent-advisory.md`
- `spec/decisions/0012-investor-mandate.md`, `0015`, `0016`, `0017`
