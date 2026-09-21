# 0005 — Agent run after US markets close

Date: 2026-09-20

## Change

Lock which session defines the daily agent advisory trigger.

## Decision

- Daily agent updates run **after US markets close** (regular NYSE/Nasdaq session), not after EU close and not “whichever is later” as an open choice.
- Applies even when the book is EU-only or mixed — one global daily advisory tick keyed off US close.
- Exact offset after the bell, timezone, and US holiday handling remain implementation detail.

## Why

Operator preference: wait until US session is done before refreshing recommendations.

## Spec touchpoints

- `spec/PROJECT.md`, `spec/domain.md`, `spec/architecture.md`
- `spec/behaviour/agent-advisory.md`
- Clarifies `0004-full-agent-context-daily`
