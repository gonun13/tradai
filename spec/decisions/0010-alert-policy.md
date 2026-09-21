# 0010 — In-app alert policy

Date: 2026-09-20

> **Superseded by `0017-alert-policy-v2.md` (2026-09-20).** The buy/sell-raises-an-alert rule
> and the "no confidence threshold" stance below were written before the sell doctrine (`0013`).
> Retained for history.

## Change

Lock when recommendations become unread in-app alerts for Stage 6.

## Decision

- Raise an `Alert` when a persisted `Recommendation` has canonical `action` ∈ {`buy`, `sell`}.
- Do **not** raise for `hold` or `watch`.
- One alert row per recommendation (`recommendation_id` unique); severity = `action`.
- Alerts are created after a succeeded or partial advisory run that wrote recommendations.
- Operator **ack** sets `unread = 0`; ack clears that row from the unread attention count.
- No confidence threshold for MVP — buy/sell alone is enough.
- In-app only (no email/push).

## Why

Spec left the threshold open; Stage 6 needs a checkable rule. Buy/sell are the actions that warrant operator attention; hold/watch are informational on the recommendations panel.

## Spec touchpoints

- `spec/domain.md`, `spec/data.md`, `spec/architecture.md`
- `spec/behaviour/agent-advisory.md`
- `spec/stages.md` Stage 6
