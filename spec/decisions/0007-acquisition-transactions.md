# 0007 — Acquisitions are transactions (date + commission in cost)

Date: 2026-09-20

## Change

Replace bare “quantity + avg cost” as the only acquisition record with **transactions** that carry trade date and commissions.

## Decision

- An **acquisition** is a `buy` transaction with:
  - `trade_date` (date of acquisition)
  - `quantity`
  - `unit_price` (native listing currency)
  - `commission` (fees/costs in native currency, ≥ 0)
  - optional notes
- **Cost basis includes commission:**  
  `lot_cost = quantity × unit_price + commission`  
  `avg_cost = lot_cost / quantity` (for that lot; holding avg is quantity-weighted across open lots).
- A **Holding** is the open position rollup for an instrument (quantity, avg_cost / total_cost derived from transactions). Deleting a holding removes its transactions; instruments (and future bars) are kept.
- MVP UI: dashboard shows **one line per ticker** (rollup). Adding the same ticker again appends a lot. Editing picks a specific acquisition when several exist.
- Sell / disposal transactions are deferred (not required for Stage 2 pass) but the `side` field may exist as `buy` | `sell` for forward compatibility.

## Why

Operator needs acquisition date and commissions in the book so P&L and agent context reflect real all-in cost, not headline fill price alone.

## Spec touchpoints

- `spec/data.md`, `spec/behaviour/portfolio.md`, `spec/tests.md`
- Complements portfolio CRUD in Stage 2
