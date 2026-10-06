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
- **Sell** (`0031`): record a sell (trade date, quantity ≤ open quantity, unit price, commission, notes) via
  `POST /holdings/{id}/sells`. FIFO consumes the oldest lots; the realised P&L is stored per sell. A partial
  sell keeps the holding line; a full exit removes it and the position moves to **Closed positions**.
- **Edit / delete any transaction** (buy or sell) via `PUT` / `DELETE /transactions/{id}`; the ledger is
  re-walked and a change that would oversell is rejected. Deleting the closing sell reopens the position.
- **Erase** (`DELETE /holdings/{id}`, `DELETE /positions/closed/{instrument_id}`) removes the instrument's whole
  history and is for entries made in error. Keep Instrument + bars.

### Read models

- Dashboard loads holdings joined with latest quotes when available; converted values and totals use the selected display currency (`0025`). Native acquisition and quote figures retain their own currencies. Missing FX shows **pending FX** and makes aggregates unavailable.
- Each holding exposes daily percentage change as the latest quote versus the most recent stored
  daily close before that quote's calendar date; it is unavailable when either value is missing.
- Missing quotes are tolerable (show holding with stale / empty quote state).
- Reporting (`0031`): unrealised P&L (today's FX), realised YTD and all-time (EUR locked at trade dates,
  converted to display at today's rate) and total = unrealised + realised all-time, for the book and per
  holding. **Closed positions** list opened/closed dates, held days, quantity, cost, proceeds, realised P&L
  and the transaction history.

## Rules

- One portfolio in v1.
- **The portfolio is one of two books.** Names the operator is considering but does not own live on the Tracker
  (`0019`, `behaviour/tracker.md`). A symbol is in exactly one book at a time: recording an acquisition for a
  tracked name archives its tracker entry, and a full exit (sell or erase) hands it back.
- EU and US listed equities and ETFs for MVP intent (crypto and non-listed funds out of scope).
- ETFs are not a second product surface — same CRUD and monitoring as equities.
- No brokerage sync in MVP.

## Acceptance cues

- After CRUD, restart Compose: holdings still present on the volume.
- After a full sell, the transactions and realised P&L remain under Closed positions.
- Agents (when run) receive holding quantities / costs as part of advisory context.

## Out of scope here

- Multi-portfolio, scenarios, broker import adapters beyond a simple enter/import path.
- Instrument lookup / autofill for the **acquisition form**. `0019` added name-or-ticker search
  (`GET /instruments/search`) for the Tracker; wiring it into this form is a natural follow-up, not done.
