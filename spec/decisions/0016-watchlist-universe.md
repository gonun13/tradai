# 0016 — Watchlist as candidate universe

Date: 2026-09-20

## Change

Add an operator-maintained watchlist so a `better_use` sell (`0013`) has something to name. Market screening is explicitly deferred.

## Decision

- New `watchlist` table: symbol / optional `instrument_id`, `note`, `added_at`.
- Watchlist candidates are passed into agent context alongside holdings.
- A `better_use` recommendation must carry `pair_symbol` naming the holding or watchlist candidate the proceeds fund. Without it the doctrine gate suppresses the sell.
- **Deferred:** open-universe market screening (the agent proposing names the operator has never considered). This needs a screener and a fundamentals feed that do not exist — note that `FMP_API_KEY` is surfaced in `/setup` but is read by nothing in `worker/`.

## Why

The agent context query is `FROM holdings INNER JOIN instruments`, so the system can only ever see what is already owned. It is structurally incapable of proposing an alternative, which makes "sell A to fund B" unreachable. A watchlist closes that gap at near-zero cost and keeps the operator in control of the universe, without blocking the doctrine on a new data pipeline.

## Spec touchpoints

- `spec/domain.md`, `spec/data.md`
- `spec/behaviour/agent-advisory.md`
- `spec/decisions/0013-sell-doctrine.md`
