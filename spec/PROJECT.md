# Tradai — Project

Personal agentic trading dashboard: live portfolio monitoring (European and US listed equities and ETFs) with scheduled AI advisory (portfolio decisions over 6 / 12 / 24 months and tracker decisions over 1 / 3 / 6 months). Advisory only — no auto-execution. Runs on local Docker at home; portfolio authority stays on the LAN, while the configured Claude/Jev providers receive the full context required for advisory.

## Purpose

Checking a stock portfolio every day is noisy and easy to miss. Tradai watches European and US holdings (listed equities and ETFs), combines market history, news, and technicals, and surfaces when a ticker looks worth buying or selling on medium horizons.

## Who

Solo operator only. No multi-user auth, SaaS tenancy, or public hosting.

## Success

You open a clear dashboard, see portfolio + live-enough market context, get scheduled agent analyses and alerts when action is warranted — without daily manual scanning or deep “pro terminal” complexity.

## Main requirements (MVP)

- Market coverage for the operator’s book: **European** venues (Euronext, Xetra/Frankfurt, and peers as data sources allow) **and US** listings (e.g. NYSE / Nasdaq) — listed equities **and ETFs** on those regular exchange venues
- Portfolio manager (CRUD holdings; local-only persistence)
- **Tracker** — a second book of names of interest, searchable by name or ticker, ingested and researched on the
  same cadence as the portfolio (`0019`)
- Live / near-live monitoring on the dashboard
- Scheduled agents that run **once per day after US markets close** (full portfolio context for decisions)
- Alerts when a recommendation crosses an “action needed” threshold
- Horizon-aware advisory: 6 / 12 / 24 months (`0014` — `3m` dropped, below the operator's holding period)
- Claude Agent CLI on **subscription auth** for agent reasoning; Jev (TypeSafe System One) for typed decisions

## Non-goals

Out of the first version (and not implied by the MVP):

- Auto-executing trades / brokerage order routing
- Crypto
- Markets outside supported EU + US exchange listings (e.g. APAC-only names) unless later expanded
- OTC / non-listed funds, mutual-fund platforms, or ETF products that are not bought as exchange-traded listings on supported markets
- Mobile / native apps
- Tax reporting
- Deep classic market-tooling (full charting suites, options chains, Level 2, complex scanners, etc.)

Also out by scale: multi-user auth, SaaS tenancy, public hosting, high-availability cloud ops.

## Later (explicitly deferred)

- Richer fundamentals depth
- More polished notification channels (email/push)
- Backtesting UI, paper-trading ledger, broker sync
- Multi-portfolio / scenarios
- Hardening beyond “works for me on this box”
- **Instrument lookup / autofill in the acquisition form** — `0019` shipped name-or-ticker search for the
  Tracker (`GET /instruments/search`, Yahoo + Finnhub + local, merged); wiring the same thing into the holdings
  form so the operator stops typing ISIN/MIC/currency by hand is the remaining half
- **Open-universe screening** — the agent proposing names the operator has never considered.
  The light per-book fundamentals feed in 0022 is not a market-wide screener; the tracker
  universe stays operator-curated (`0016`, `0019`)

## Constraints (product-level)

- ~1-week prototype mindset: ship a thin vertical slice of the happy path first
- Personal use only; not investment advice to others
- Secrets and portfolio data stay on the home machine / LAN
- Claude usage via subscription OAuth — not `ANTHROPIC_API_KEY` pay-as-you-go
- **All money shown in the UI is in EUR** (convert non-EUR listings for display)

## Happy path (acceptance shape)

1. Run `docker compose up` on the home machine and open the web UI.
2. Configure market-data / news API keys and Jev credentials once; Claude Agent CLI is already signed in with the Claude subscription (`claude auth login`, or `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` for unattended Docker — **no** `ANTHROPIC_API_KEY`).
3. Enter or import the portfolio (EU and/or US ISINs / tickers, quantities, cost basis) — stored only in the local database.
4. Dashboard shows holdings, delayed/live quotes, and recent agent output at a glance.
5. **Once per day after US markets close**, agents pull prices/news/technicals with **full portfolio context**, run analysis via Claude Agent CLI + Jev, and write recommendations + alerts locally.
6. When something needs attention, an in-dashboard alert appears — the operator decides and trades elsewhere.

## Open (do not invent answers in code)

See open items in `spec/`: notifications beyond in-app and day-one MIC set. Claude auth is locked in
`0008`, the current alert policy in `0018`, and the US-close clock in `0011`.

Research-data routing is locked in `spec/decisions/0022-four-layer-ingestion.md` and the
fundamentals revision in `spec/decisions/0023-free-fundamentals-routing.md`; FX remains
the auxiliary Frankfurter service chosen in `0006`.
