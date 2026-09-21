# 0001 — Support ETFs on regular European markets

Date: 2026-09-20

## Change

Extend MVP instrument scope from equities-only wording to **listed equities and ETFs**.

## Decision

- ETFs are first-class holdings alongside stocks.
- They are bought on the **same regular European markets** already in scope (Euronext, Xetra/Frankfurt, peers as data allows) — not a separate ETF venue, OTC fund platform, or mutual-fund integration.
- Same data path: Instrument (+ `kind`), quotes/bars, agent advisory, alerts.
- Still out at time of writing: US-listed ETFs, crypto, non-exchange-traded funds.
- **Superseded in part by `0002-us-listings`:** US-listed stocks and ETFs are now in scope; crypto and non-listed funds remain out.

## Why

Operator book includes ETFs; treating them as ordinary exchange listings matches how they are actually traded and keeps the architecture thin.

## Spec touchpoints

- `spec/PROJECT.md`, `spec/domain.md`, `spec/data.md`
- `spec/behaviour/portfolio.md`, `spec/behaviour/agent-advisory.md`
