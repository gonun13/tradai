# MVP stages

Verifiable increments toward the happy path in `PROJECT.md`. Operator checks each stage before the next starts.

When stages disagree with other specs, higher-priority `spec/` docs win; update this file if stage scope changes.

**Status: MVP closed** (2026-09-20) — Stages 1–6 and 5b verified against running Compose stack.
Stage 7 (investor profile) and Stage 8 (tracker, `0019`) verified the same way.

## Stage 1 — Compose skeleton

**Goal:** Three services + SQLite volume boot on LAN; health visible.

**Done when:**
- [x] `docker compose up --build` starts `ui`, `api`, `worker`
- [x] `GET http://127.0.0.1:8080/health` returns JSON ok
- [x] Browser `http://127.0.0.1:3000` shows Tradai shell and API health
- [x] Worker logs a periodic heartbeat (no market calls yet)
- [x] Named volume mounted; data dir survives `docker compose down` (not `down -v`)

## Stage 2 — Portfolio CRUD

**Goal:** Local holdings authority via Slim + SQLite; Nuxt can manage the book.

**Done when:**
- [x] Create / update / delete holdings via acquisition fields (symbol, ISIN optional, MIC, currency, kind, **trade date**, qty, **unit price**, **commission**, notes)
- [x] Cost basis includes commission (`lot_cost = qty × price + commission`)
- [x] List holdings after refresh and after Compose restart (same volume)
- [x] UI money labels assume EUR display path exists (native currency stored; conversion can stub until Stage 3)

**How to check:** `bin/up -d` → open `http://127.0.0.1:3000` → add a holding → `bin/down` → `bin/up -d` → holding still listed.
Also: `curl -s http://127.0.0.1:8080/holdings`
## Stage 3 — Quotes, bars, FX

**Goal:** Capability × region Historical + FX adapters (`0006`); dashboard shows live-enough values in EUR.

**Done when:**
- [x] Finnhub (US) + yfinance (EU) fetch quotes/bars for book instruments
- [x] Frankfurter converts non-EUR notionals for display
- [x] Quotes cached in SQLite; UI still shows last cache if a vendor errors
- [x] Optional IBKR quotes left stubbed or behind a feature flag (not required to pass stage)

**How to check:** `cp .env.example .env` and set `FINNHUB_API_KEY` for US → `bin/up -d` → UI **Refresh quotes** → EUR cost/value/P&L. EU tickers (e.g. `AM.PA`) use yfinance without a key.

**Note:** Finnhub free plans often block `/stock/candle`; US quotes still come from Finnhub and bars fall back to yfinance.

## Stage 4 — News + technicals

**Goal:** Agent-ready market features without Claude yet.

**Done when:**
- [x] Marketaux news cached for book tickers (US + EU)
- [x] Local technicals computed from OHLCV and stored or attached to run context preview
- [x] Manual “ingest now” (or scheduled tick) refreshes quotes + news + technicals

**How to check:** Set `MARKETAUX_API_TOKEN` in `.env` → `bin/up -d` → UI **Ingest now** → technicals + news on dashboard; `curl -s http://127.0.0.1:8080/context/preview`.
## Stage 5 — Agent advisory

**Goal:** Full-book context → Claude Agent CLI + Jev → recommendations.

**Done when:**
- [x] “Run now” creates an `AgentRun` and per-instrument `Recommendation` rows (buy/sell/hold/watch × horizons)
- [x] Payload includes holdings sizes/costs (per `0004`), not ticker-only stubs
- [x] Failed runs leave visible failed/partial status
- [x] No `ANTHROPIC_API_KEY` required on the intended path

**How to check:** Set `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`) and `TYPESAFE_API_KEY` in `.env` → `bin/up -d` → UI **Run now** → recommendations panel; or `curl -s -X POST http://127.0.0.1:8080/agent/run` then `curl -s http://127.0.0.1:8080/agent/runs/latest`. Missing tokens should show a **failed** run with a clear error (not a silent no-op).

**Note:** Stage 5 MVP may remain a one-shot Claude→Jev pipeline. The target researcher/decider loop is Stage **5b** (`0009`).

## Stage 5b — Claude researcher / Jev decider loop

**Goal:** Multi-lens Jev decisions, bounded Claude↔Jev rounds, info-needs, and per-ticker conversation logs (`0009`).

**Done when:**
- [x] Claude researches and builds Jev requests; Jev alone sets `action` from the **combined** lens
- [x] Jev also runs `news` and `technicals` lenses plus one more; supporting answers stored on the recommendation
      (the third was `prices` until Stage 7 replaced it with `thesis` — see `0013`)
- [x] Configurable Claude-driven scenario rounds after the initial four-lens pass (default 0, maximum 10; cap enforced)
- [x] `AgentRun` stores research + info-needs; Recommendations store conversation transcripts
- [x] UI: log icon per ticker opens that instrument’s Claude↔Jev transcript for the run
- [x] Recommendation chip / primary action matches combined lens

**How to check:** `bin/up -d` → UI **Run now** → recommendations show combined actions → click log icon on a ticker → see Claude requests and Jev answers (lenses + any scenario rounds) → latest run exposes info-needs for ingest planning.

## Stage 6 — Alerts + daily schedule + setup

**Goal:** Close the happy path: attention signals, post–US-close schedule, key setup.

**Done when:**
- [x] Simple alert policy raises in-app alerts from recommendations; ack clears unread
- [x] Scheduler runs advisory once after US close (exact offset documented in `.env`)
- [x] Setup screen / env docs for Finnhub, Marketaux, FMP (optional), Jev, Claude token
- [x] End-to-end: compose up → add holdings → see quotes → run now → see recs/alerts

**How to check:** `bin/up -d` → UI **Setup** shows key presence → **Run now** (or wait for post–US-close) → buy/sell rows appear under **Alerts** → **Ack** clears unread. Schedule: `ADVISORY_AFTER_US_CLOSE_MINUTES` (default 30 → 16:30 ET). Policy: `0018` (building on `0010`/`0017`). Clock: `0011`.

## Stage 7 — Investor profile

**Goal:** Give Claude and Jev an objective to reason with, without turning the operator into an approver.

The agents had no mandate: Claude's brief ended in "Be concise" and Jev was asked for "the best
overall advisory action" with *best* undefined anywhere in the repo. Asked an unanchored question
about a holding that is down, a model says sell.

A first pass at fixing this (`0012`–`0017`) added a code-enforced sell gate plus manual thesis
approval and watchlist curation — which the operator rejected as overbuilt: *"I just want to load
an investor profile and Claude and Jev are the intelligence."* `0018` keeps the useful context and
removes the workflow.

**Done when:**
- [x] Separate free-text **investor** and **portfolio** profile fields (Setup page), sent verbatim into
      the applicable prompts; each falls back to a default when unset (`0012`, revised by `0018`/`0019`)
- [x] Sell-doctrine guidance (thesis broken / better use, not price alone) given to Jev and Claude as
      prompt instructions — **not enforced in code**; whatever Jev decides is written (`0013`, `0018`)
- [x] Jev's choice set still carries the reason (`sell_thesis_broken`, not bare `sell`) — free
      structure, no gate attached to it
- [x] Horizons `6m / 12m / 24m` (`0014`)
- [x] Claude drafts a thesis per holding automatically, used the same run — no approval step
      (`0015`, revised by `0018`)
- [x] Cash reserve + realised-gains-YTD as context, editable on the Setup page; no watchlist required
      for a `better_use` sell (`0016`, revised by `0018`)
- [x] `HoldingRepository` rollup fixed — it filtered `side = 'buy'`, so sells never reduced the book
- [x] Cost-basis weight alongside market-value weight, and `held_days` from the transaction log
- [x] Alert policy simplified back toward `0010`: any buy/sell, plus a reported thesis break
- [x] `GET /agent/outcomes` scores matured recommendations against subsequent prices

**How to check:** `bin/up -d --build` → `/setup` → write an investor profile, set cash, save →
**Run now** → every recommendation carries a `reason`; sells carry an informational `loss_gate`.
`docker compose exec worker python test_persist_e2e.py` confirms the persisted action always
matches exactly what Jev decided — nothing is rewritten or suppressed.
Unit cases: `docker compose exec worker python test_doctrine.py`.

## Stage 8 — Tracker: a second book (`0019`)

- [x] `watchlist` → `tracker`: `instrument_id` mandatory, `archived_at` for auto-promote,
      existing rows migrated with their notes and an instrument minted where missing
- [x] Name-or-ticker search — `GET /instruments/search`, local instruments first then Yahoo +
      Finnhub merged; each vendor tried independently so one being down degrades, not fails
- [x] Tracker CRUD (`GET/POST /tracker`, `PUT/DELETE /tracker/{id}`); adding a symbol that is
      already a holding is a 409
- [x] Auto-promote: recording an acquisition archives the tracker entry; deleting the holding
      hands it back, note intact
- [x] Ingestion covers both books — quotes, bars, technicals. News stays holdings-only to
      protect the Marketaux quota
- [x] One combined daily run: context carries `holdings` + `tracked`; `_subjects()` is the
      single iteration point, and it drops tracked names from the `news` lens
- [x] Tracker choice set (`buy_now` / `wait_better_entry` / `keep_watching` /
      `drop_lost_interest`) and the `drop` action; `recommendations` rebuilt for the new
      CHECKs plus a stored `book` column
- [x] Profiles split: investor (judges the tracker, travels everywhere) and portfolio (judges
      holdings). Existing text moved to the portfolio half so nothing regressed
- [x] UI: `default` layout with Portfolio | Tracker tabs, shared `RecommendationsPanel` filtered
      per book, tracker table with price / 1m / 3m / RSI14 / note
- [x] Tracker horizons `1m / 3m / 6m` (`0020`), decided independently of holdings' `6m / 12m /
      24m`; `return_6m_pct` added to technicals and a `6m` column added to the tracker table

**How to check:** `bin/up -d --build` → `/tracker` → search "airbus", add `AIR.PA` with a note
→ **Ingest now** (its quote, bars and technicals appear; it is absent from the news section) →
**Force run** → the Tracker tab shows its own recommendations, three lenses not four, and
`/log/AIR.PA` shows "Tracked, not owned" with the investor profile as the mandate. Record an
acquisition for it and it moves to the Portfolio tab; delete the holding and it comes back.
`docker compose exec worker python test_persist_e2e.py` covers the tracker verbs, the `book`
column and the three-lens rule; `test_doctrine.py` covers the choice maps and `compose_mandate`.

## Explicitly later (not these stages)

IBKR production wiring, rich fundamentals, email/push, backtesting, broker portfolio sync.

**Open-universe screening** — the tracker is operator-curated: the agents reason about names the
operator put there, and a `better_use` sell can now name one. Having the agent *propose* names
the operator has never considered still needs a screener and a fundamentals feed.
`FMP_API_KEY` is surfaced in `/setup` but read by nothing in `worker/`.

**Instrument lookup / autofill in the acquisition form** — `0019` shipped the search endpoint and
wired it into the Tracker's add form. The holdings form still makes the operator type ISIN, MIC
and currency by hand (see `PROJECT.md` Later).
