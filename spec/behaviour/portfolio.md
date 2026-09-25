# Behaviour — Portfolio

Local holdings management for European and US listed equities and ETFs. Slim API is the write authority; Nuxt is the operator UI.

## Goals

- Capture the operator’s book (ISINs / tickers, quantities, cost basis) once and keep it accurate — stocks and ETFs bought on regular EU and US markets.
- Persist only on local SQLite. Position sizes, cost basis and portfolio value **are** sent to the configured
  agent/LLM providers — this is the deliberate tradeoff locked in `decisions/0004-full-agent-context-daily.md`
  (richer advice over ticker-only privacy). No cloud portfolio database; no third parties beyond those providers.
- **Display one holding line per ticker**; multiple acquisitions for the same symbol are stored as separate transactions and roll up (quantity-weighted average cost). Editing still targets a chosen acquisition lot.

## Flows

### Setup / first run

1. Operator brings up Compose and opens the UI.
2. Operator enters or imports holdings via **acquisition transactions** (instrument identity + kind + trade date + quantity + unit price + commission + optional notes).
3. System resolves or creates Instrument records, stores Transactions, and maintains Holding rollups locally (same flow for ETFs and stocks).
4. Dashboard reflects the book without requiring a cloud account.

### CRUD holdings

- **Create / update / delete** holdings via Slim HTTP API; create/update capture acquisition **trade_date** and **commission** (cost basis includes commission — `0007`).
- Adding another acquisition for an **existing ticker** appends a transaction and recomputes the holding rollup (still one dashboard line).
- Updating requires choosing a **transaction_id** when multiple lots exist; only that lot is rewritten, then the rollup is recomputed.
- Deleting a holding removes all of its transactions; keep Instrument + bars unless product rules say otherwise (default: keep Instrument + bars).

### Read models

- Dashboard loads holdings joined with latest quotes when available; **values and totals in EUR**.
- Each holding exposes daily percentage change as the latest quote versus the most recent stored
  daily close before that quote's calendar date; it is unavailable when either value is missing.
- Missing quotes are tolerable (show holding with stale / empty quote state).

## Rules

- One portfolio in v1.
- **The portfolio is one of two books.** Names the operator is considering but does not own live on the Tracker
  (`0019`, `behaviour/tracker.md`). A symbol is in exactly one book at a time: recording an acquisition for a
  tracked name archives its tracker entry, and deleting the holding hands it back.
- EU and US listed equities and ETFs for MVP intent (crypto and non-listed funds out of scope).
- ETFs are not a second product surface — same CRUD and monitoring as equities.
- No brokerage sync in MVP.

## Acceptance cues

- After CRUD, restart Compose: holdings still present on the volume.
- Agents (when run) receive holding quantities / costs as part of advisory context.

## Out of scope here

- Multi-portfolio, scenarios, broker import adapters beyond a simple enter/import path.
- Instrument lookup / autofill for the **acquisition form**. `0019` added name-or-ticker search
  (`GET /instruments/search`) for the Tracker; wiring it into this form is a natural follow-up, not done.
