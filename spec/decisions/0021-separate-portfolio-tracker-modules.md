# 0021 — Separate Portfolio and Tracker modules

Date: 2026-09-24

**Revises `0019`'s tab-based UI.** The data model, combined ingest and combined advisory run are
unchanged.

## Change

- Portfolio and Tracker become canonical application areas at `/portfolio` and `/tracker`.
  `/` redirects to Portfolio, preserving query parameters.
- A neutral application utility bar holds the module switcher and the shared operational tools.
  The switcher is navigation between modules, not a tab strip inside one dashboard.
- Portfolio uses an owned-capital ledger identity; Tracker uses an entry-research identity. They
  remain one product family through shared typography, components and interaction patterns.
- Recommendation logs are book-scoped routes. Alerts expose the existing recommendation `book`
  in their read model so the global alert surface can label and route them correctly.
- Tracker-to-Portfolio promotion remains an explicit cross-module handoff through the acquisition
  form. No business rule, persistence contract or agent orchestration changes.

## Why

The two books use similar market and advisory machinery but answer different operator questions:
what to do with owned capital versus whether to start a position. Presenting them as neighboring
tabs overemphasised implementation sharing and made Tracker appear subordinate to Portfolio.
Separate workspaces make the mental model visible without duplicating the toolset or splitting the
single run that needs both books in context.

## Test policy

All project builds and test commands run in Docker. Browser acceptance uses the available Docker
Playwright MCP against the exposed UI; host-installed Node, PHP and Python runtimes are not part of
the verification path. See `spec/tests.md`.

