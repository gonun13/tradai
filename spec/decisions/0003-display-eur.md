# 0003 — Display currency is EUR

Date: 2026-09-20

## Change

Resolve mixed-currency display for EU + US books.

## Decision

- **All UI money is shown in EUR** — portfolio totals, position values, cost summaries, aggregated P&L.
- Listings may still trade/quote in USD (or other); convert for presentation.
- Persist native currency where useful; no multi-currency toggle in the UI for v1.
- FX rate **source** = Frankfurter (see `0006`); refresh cadence remains implementation open (good enough for a personal dashboard, not trading-grade FX).

## Why

Operator wants a single currency view; EUR matches the home bookkeeping frame after adding US listings.

## Spec touchpoints

- `spec/PROJECT.md`, `spec/domain.md`, `spec/data.md`
- `spec/behaviour/portfolio.md`, `spec/behaviour/monitoring.md`
- Clarified by `0006-market-data-adapters` (Frankfurter)
