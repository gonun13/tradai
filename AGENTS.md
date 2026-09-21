# AGENTS.md

## Spec-driven development

Authoritative product and engineering intent lives under `spec/`.
When specs disagree, respect them in this order (higher wins):

1. `spec/PROJECT.md`
2. `spec/domain.md`, `spec/architecture.md`
3. `spec/data.md`
4. `spec/behaviour/*`, `spec/decisions/*` (add decision records when changing the initial spec)
5. `spec/tests.md` (when present)

Implement and review against these docs before inventing behaviour.

### Present in this repo

- `spec/PROJECT.md` — goals, non-goals, MVP, happy path
- `spec/domain.md` — entities, advisory semantics, privacy invariants
- `spec/architecture.md` — components, stack, boundaries, NFRs
- `spec/data.md` — persistence and entity contracts
- `spec/behaviour/portfolio.md`
- `spec/behaviour/tracker.md`
- `spec/behaviour/monitoring.md`
- `spec/behaviour/agent-advisory.md`
- `spec/decisions/0001-support-etfs.md`
- `spec/decisions/0002-us-listings.md`
- `spec/decisions/0003-display-eur.md`
- `spec/decisions/0004-full-agent-context-daily.md`
- `spec/decisions/0005-after-us-close.md`
- `spec/decisions/0006-market-data-adapters.md`
- `spec/decisions/0007-acquisition-transactions.md`
- `spec/decisions/0008-claude-docker-oauth-token.md`
- `spec/decisions/0009-claude-researcher-jev-decider.md`
- `spec/decisions/0010-alert-policy.md`
- `spec/decisions/0011-us-close-schedule-offset.md`
- `spec/decisions/0012-investor-mandate.md`
- `spec/decisions/0013-sell-doctrine.md`
- `spec/decisions/0014-horizons-6-12-24.md`
- `spec/decisions/0015-thesis-of-record.md`
- `spec/decisions/0016-watchlist-universe.md`
- `spec/decisions/0017-alert-policy-v2.md` (supersedes `0010`)
- `spec/decisions/0018-simplify-trust-agents.md` (revises `0012`–`0017`)
- `spec/decisions/0019-tracker-as-second-book.md` (revises `0016`; partially reverses `0018`)
- `spec/decisions/0020-tracker-horizons-1-3-6.md` (tracker-only horizon split)
- `spec/stages.md` — verifiable MVP implementation stages


### Extending

- Add `spec/ui-ux.md` when look-and-feel / a11y is worth locking.
- Add `spec/tests.md` when acceptance / QA procedure is defined.
- Add `spec/decisions/NNNN-short-title.md` when a change revises the initial spec (do not dump chat preferences).
- If historical design notes are added, keep them in `DESIGN.md`; prefer updating `spec/` when intent changes.

Do not invent entities, APIs, or behaviours that specs mark unknown or that the operator has not affirmed.
