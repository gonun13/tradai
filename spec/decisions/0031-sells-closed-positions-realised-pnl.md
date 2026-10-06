# 0031 — Sells, closed positions and realised P&L

Date: 2026-10-05

**Revises `0007`** (sell transactions are live) and the delete rule in `behaviour/portfolio.md`.
Keeps `0019` (a full exit hands the name back to the Tracker) and `0013` (FIFO, Portugal).

## Change

- **Sells are recorded.** A sell is a `side = 'sell'` transaction with trade date, quantity, unit
  price, commission and notes. Partial and full exits are both sells. Proceeds are
  `quantity × unit_price − commission`; realised P&L is proceeds minus the FIFO cost of the
  consumed lots (commission already capitalised into buy cost, `0007`).
- **A Holding is the open position only.** A `holdings` row exists if and only if the FIFO open
  quantity is above zero. A full exit removes the rollup row; the transactions and disposals stay.
  Recording a buy for a closed instrument reopens it on the same history.
- **Closed positions** are derived from the ledger: instruments with transactions and no open
  holding. They show opened/closed dates, quantity, cost, proceeds and realised P&L, and keep
  their transaction list.
- **Any transaction can be edited or deleted.** Every change re-walks the FIFO ledger. A change that
  would sell more than is open at that date (oversold) is rejected and nothing is written.
- **Realised P&L is locked in EUR at trade dates.** Each transaction stores the trade-date
  `fx_to_eur` (Frankfurter, via the worker). Disposals store `proceeds_eur`, `cost_eur` and
  `realized_pnl_eur`, with buy lots at their own buy-date rate. A display currency other than EUR
  converts the locked EUR amount at today's EUR → display rate. A missing trade-date rate never
  blocks a write; the EUR figures stay `null` (pending FX) and are retried on the next ledger write
  and after each manual ingest.
- **Unrealised P&L is unchanged:** cost and market value both convert at today's FX, so agent
  context keeps its meaning.
- **Totals:** realised YTD, realised all-time, unrealised, and total = unrealised + realised
  all-time, all in the display currency. Any pending constituent makes that total unavailable.
- **Delete becomes erase.** `DELETE /holdings/{id}` and `DELETE /positions/closed/{instrument_id}`
  remove the instrument's whole history (transactions and disposals) and are meant for entries
  made in error. Exiting is a sell.
- **Tracker hand-back.** A sell or transaction change that closes a position unarchives its Tracker
  entry (as delete already did, `0019`); one that reopens it archives the entry again.
- **Agents** still receive open holdings only, plus realised gains YTD, which now comes from real
  disposals. That feeds the `offset_same_year` loss gate (`0013`) with no prompt change. Closed
  positions are not sent (`0027` budget).

## API

- `POST /holdings/{id}/sells`: record a sell; returns the holding (or `null` when closed), `closed`,
  and the new disposal.
- `PUT /transactions/{id}` and `DELETE /transactions/{id}`: edit or remove any transaction.
- `GET /positions/closed`: the closed positions read model.
- `DELETE /positions/closed/{instrument_id}`: erase a closed position's history.
- `GET /holdings` adds `summary` with `unrealized_pnl_display`, `realized_ytd_display`,
  `realized_all_time_display` and `total_pnl_display`. Each holding adds `realized_pnl_display`
  and `total_pnl_display`.
- `GET /portfolio/settings` adds `realized_gains_all_time_from_disposals_display`.

## Out of scope

Dividends and income, lot methods other than FIFO, trade-date FX on unrealised cost (a possible
follow-up so that unrealised → realised has no FX jump), sending closed positions to agents, and
broker import.

## Why

The operator wants to exit positions and still keep the transaction history, and to report
unrealised, realised and all-time results. Tax residence is Portugal (`0012`), where gains on
foreign-currency securities are measured in EUR at the transaction dates.
