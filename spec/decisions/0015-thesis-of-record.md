# 0015 — Thesis of record

Date: 2026-09-20

## Change

Introduce a stored, versioned, operator-approved investment thesis per holding. A `thesis_broken` sell (`0013`) is impossible without one.

## Decision

- New `theses` table: `instrument_id`, `thesis`, `falsifiers` (JSON array of named, checkable conditions), `status` ∈ `draft | approved | superseded`, `version`, timestamps.
- **Claude drafts, operator approves.** For any holding lacking an `approved` thesis, the researcher pass proposes one from available evidence and writes it `status='draft'`. The operator accepts, edits, or rewrites it in the UI.
- Editing an approved thesis creates a **new version** and supersedes the prior one. History is kept — a thesis that changed after the price moved is itself a signal.
- Each run, Claude tests the approved thesis against new evidence and emits `thesis_status` ∈ `intact | weakening | broken`, **with the specific evidence that moved it**. An assertion without evidence does not count.
- **Fails closed:** a holding with no approved thesis is ineligible for a `thesis_broken` sell. It can only be held or watched.

## Why

"Thesis broken" is one of the two sell reasons the operator accepts, and it was uncomputable — nothing recorded why any position was owned. `holdings.notes` is free text, unstructured, and never reached the models as something to test against.

Drafting is delegated to Claude because the alternative (the operator writing eight theses by hand) is the kind of upfront cost that stalls adoption. Approval stays with the operator because an agent-invented thesis that the operator never endorsed is not a thesis — it is a rationalisation, and one that would drift to match the price.

## Spec touchpoints

- `spec/domain.md` — new entity
- `spec/data.md` — `theses` contract
- `spec/behaviour/agent-advisory.md`
- `spec/decisions/0013-sell-doctrine.md`
