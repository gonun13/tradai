# 0002 — Include US-listed stocks and ETFs

Date: 2026-09-20

## Change

Expand market scope from European-only listings to **European and US** listed equities and ETFs.

## Decision

- US exchange listings (stocks and ETFs, e.g. NYSE / Nasdaq) are in MVP scope alongside European venues.
- Same instrument model (`kind` equity|etf), quotes/bars, advisory, and alerts — no separate US product surface.
- Crypto and non-exchange-traded funds remain out.
- Markets beyond supported EU + US listings remain out unless later expanded.
- Market-data adapters must cover both regions (one or more providers); scheduling should respect both market calendars as needed.
- Supersedes the “US listings out” clause in `0001-support-etfs` and earlier DESIGN non-goals.

## Why

Operator book includes US-listed stocks and ETFs; excluding them forced an artificial split from how the portfolio is actually held.

## Spec touchpoints

- `spec/PROJECT.md`, `spec/domain.md`, `spec/data.md`, `spec/architecture.md`
- `spec/behaviour/portfolio.md`, `spec/behaviour/monitoring.md`
