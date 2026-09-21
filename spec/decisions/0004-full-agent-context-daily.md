# 0004 — Full agent portfolio context; daily post-close runs

Date: 2026-09-20

## Change

1. Relax ticker-only agent privacy: agents get full portfolio access for better decisions.
2. Lock agent recommendation cadence to once per day after market close.

## Decision

- Claude Agent CLI / Jev (and equivalent) payloads **include holdings**: quantities, cost basis, weights/concentration, P&L, EUR totals, plus market features.
- Portfolio remains stored only on local SQLite / LAN (no cloud DB); sharing with the configured agent providers is an accepted personal-use tradeoff.
- Agent **updates** (new recommendations / alerts from analysis) run **once per day after market close**, not continuous intraday advisory.
- Quote monitoring may still poll during market hours.
- Optional manual “run now” remains allowed for catch-up/debug; it does not replace the daily schedule.
- For mixed EU+US books, run after US close (clarified / locked in `0005-after-us-close`); exact cron minutes/timezone open.
- Supersedes earlier DESIGN / spec rules that forbade position sizes in outbound agent calls.

## Why

Operator prefers decision quality over ticker-only privacy, and wants a predictable once-daily advisory cycle after the session ends.

## Spec touchpoints

- `spec/PROJECT.md`, `spec/domain.md`, `spec/data.md`, `spec/architecture.md`
- `spec/behaviour/agent-advisory.md`, `spec/behaviour/portfolio.md`
