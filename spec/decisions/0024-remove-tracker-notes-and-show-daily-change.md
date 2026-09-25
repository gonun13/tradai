# 0024 — Remove Tracker notes and show daily change

Date: 2026-09-25

Revises the operator-note parts of `0019` and the per-name-run exclusion in
`behaviour/tracker.md`.

## Change

Tracker entries no longer accept, expose, edit, display, or send an operator note to the advisory
agents. The legacy database column may remain so existing installations can upgrade without a
destructive migration, but it is outside the active data contract.

The Tracker table adds **Daily %**, calculated as the latest quote versus the most recent stored
daily close before the quote's calendar date. The cell uses a subtle green background for a gain,
red for a loss, and neutral for zero or unavailable data.

The existing per-name **Run** and **Remove** row actions become compact icon controls. Each keeps
an explicit accessible label and tooltip; **Bought** remains text because it describes a domain
transition rather than a generic operation.

## Why

The Tracker is a monitored research book rather than a note-taking surface. Removing notes keeps
entry creation and scanning focused, while daily movement adds the most immediately useful market
signal. Icons reduce action-column width without hiding meaning from assistive technology.
