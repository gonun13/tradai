# 0018 — Simplify: one profile, no approval workflow, agents decide

Date: 2026-09-20

**Revises `0012`–`0017`** in response to direct operator feedback: the mandate/thesis/
watchlist machinery those decisions specified became an approval bureaucracy (draft/approve
theses, curate a watchlist, a `/doctrine` settings page) before anything worked. The operator
wants to load one investor profile and have Claude and Jev be the intelligence — not a
workflow they have to operate.

## Change

- **Investor profile → one free-text field.** `settings.investor_profile_text`, written once
  on the Setup page. Sent verbatim into every Claude and Jev prompt as the mandate. Falls
  back to a short default (`doctrine.DEFAULT_MANDATE`) when empty, so the system still works
  before the operator writes anything.
- **No approval workflow.** Claude still drafts a thesis per holding (`0015`) and Jev's
  choice set still carries a reason (`sell_thesis_broken`, not bare `sell`) so a sell always
  says why — that structure is free and stays. What's gone is the requirement that a human
  approve it before it counts, or that a `better_use` sell name a pre-curated watchlist entry.
  Claude's thesis is written straight in and used the same run.
- **No code-level sell gate.** `worker/doctrine.py` no longer validates or suppresses
  anything. It parses Jev's composite choice into (action, reason) and computes an
  informational `loss_gate` label (same-year offset / no-recovery-24m) for the log page —
  neither changes what gets persisted. **Whatever Jev decides is what gets written.** The
  sell-doctrine guidance (thesis broken / better use, not price alone) is now given to Jev
  and Claude as prompt instructions to reason with, the same way the rest of the mandate is.
- **Alerts simplified back toward `0010`:** any buy or sell alerts, plus a thesis reported
  `broken`. No confidence floor, no suppression filter (nothing is suppressed).
- **`/doctrine` page removed.** Cash reserve and realised-gains-YTD move to the Setup page,
  next to the profile text. Watchlist UI removed (the `watchlist` table/routes still exist,
  unused — a `better_use` target is just whatever Claude names, not validated against a list).

## What's kept from 0012–0017

The context enrichment was real value, not bureaucracy, and stays: horizons `6m/12m/24m`
(`0014`), the FIFO cost-basis fix and `held_days`/cost-weight in context, the `thesis` lens
replacing `prices` (`0013`), realised-gains-YTD computed from the transaction ledger, and
outcome tracking (`GET /agent/outcomes`). None of that requires the operator to do anything.

## Why

Operator: *"why did you make everything so complicated?! I don't want to approve doctrines
and theses. I just want to load an investor profiler and Claude and Jev are the
intelligence. KISS."* Enforcement-in-code was the operator's own earlier call (`0013`), made
before seeing what it cost to operate. Given richer context and a stated profile, trusting
the models to apply the doctrine themselves is a legitimate, simpler design — and is what
was asked for once the cost was visible.

## Spec touchpoints

- Revises `spec/decisions/0012` (profile becomes free text, not structured fields)
- Revises `spec/decisions/0013` (no code enforcement; guidance moves to prompts)
- Revises `spec/decisions/0015` (no approval gate; auto-written and used same run)
- Revises `spec/decisions/0016` (no watchlist requirement)
- Revises `spec/decisions/0017` (alerts simplified; no confidence floor / suppression filter)
- `spec/behaviour/agent-advisory.md`, `spec/domain.md`
