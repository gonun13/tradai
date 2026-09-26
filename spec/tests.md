# Tests

Audience: QA / operator regression. How to verify Tradai still matches `PROJECT.md` and the behaviour specs.

Setup and day-to-day running live in the root `README.md`. This file is acceptance and regression — not a product stage plan.

## When to test

| Trigger | Depth |
| --- | --- |
| First clone / Compose change | Smoke + portfolio CRUD |
| Market-data / ingest change | Quotes, FX, news, technicals |
| Advisory / Claude / Jev change | Manual run + worker automated tests |
| Alert / schedule change | Alerts + schedule arming in worker logs |
| Tracker / profile / thesis change | Tracker + Setup flows + `test_persist_e2e.py` |
| Before sharing a release | Full manual happy path below |

Prefer `bin/up -d` for checks. Use `bin/down -v` only when you intend to wipe the SQLite volume.

## Execution environment

- **Everything runs in Docker.** Build application images with Compose and run project scripts,
  Python, PHP, Node and HTTP probes inside the appropriate container. Do not use host-installed
  application runtimes for verification.
- The host is only an orchestrator for `docker compose` / `bin/*` and the available **Docker
  Playwright MCP**.
- Browser acceptance, responsive checks and visual regression are performed with the Docker
  Playwright MCP against `http://127.0.0.1:3000`.
- API probes run from the worker container over the Compose network, for example
  `docker compose exec worker curl -fsS http://api:8080/health`.

## Prerequisites

```bash
cp .env.example .env   # if needed
```

| Capability under test | Env |
| --- | --- |
| Compose + portfolio only | none |
| US quotes | `FINNHUB_API_KEY` |
| Fundamentals | `FINNHUB_API_KEY` for US equities; `ALPHA_VANTAGE_API_KEY` for EU equities and ETFs |
| News | `MARKETAUX_API_TOKEN` |
| Advisory | `CLAUDE_CODE_OAUTH_TOKEN`, `TYPESAFE_API_KEY` — **never** `ANTHROPIC_API_KEY` |

## Smoke

1. `docker compose build` then `bin/up -d`
2. `docker compose exec worker curl -fsS http://api:8080/health` → JSON with `"ok": true`
3. Docker Playwright MCP opens `http://127.0.0.1:3000` → redirects to `/portfolio`; Portfolio loads
4. Docker Playwright MCP opens `/tracker`; Tracker has a distinct module identity and shared utility bar
5. `docker compose logs worker --tail 20` — periodic `[tradai-worker] heartbeat`
6. `bin/down` then `bin/up -d` — data under `tradai-data` still present (unless you used `-v`)

## Acceptance by area

### Portfolio (`behaviour/portfolio.md`)

1. UI: add / edit / delete a holding (acquisition: trade date, qty, unit price, commission)
2. `docker compose exec worker curl -fsS http://api:8080/holdings` — book JSON
3. Restart Compose — holding still present
4. Money labels use the selected display currency (EUR by default)

### Quotes, bars, FX (`0006`)

1. Set Finnhub for US names; EU names (e.g. `AM.PA`) work via yfinance without a key
2. UI → **Ingest now** (or Refresh quotes)
3. Converted cost / market value / P&L fill in for EUR, USD, GBP, and CHF; missing FX shows pending and does not produce a partial aggregate

**Note:** Finnhub free plans often block `/stock/candle`; US quotes may come from Finnhub with bars falling back to yfinance.

### Multi-horizon history (`0026`)

1. Ingest a US and European name: each instrument receives one compact adjusted series; SPY and
   EXSA.DE are each fetched once for their region and are reported independently.
2. Re-ingest before a week: all long-history series come from cache. Force a provider failure or
   truncated response: existing points remain and normal market ingestion succeeds.
3. `GET /instruments/{id}/history?range=1y|2y|5y|max` returns adjusted basis, source/as-of/coverage,
   native currencies, resolutions, normalized stock/benchmark arrays, comparison start and returns.
4. Verify different inception dates, missing benchmark warning, invalid range 400, and unknown
   instrument 404. Confirm `price_bars`, daily change, technicals, and outcome scoring are unchanged.
5. In both modules open a row chart, switch all ranges, open a second row (the first closes), and
   check loading/empty/stale/warning states, keyboard focus, narrow rendering, palettes, and
   solid/dashed non-color-only identification.
6. Confirm `/context/preview` and a newly persisted advisory context contain no long-history points.

### News + technicals

1. Set `MARKETAUX_API_TOKEN`
2. **Ingest now** — technicals (RSI/SMA), fundamentals when a route is available, and
   daily-cached news on holdings and tracked names
3. **Ingest logs** reports ingested vs cached vs missing instrument datasets for Historical,
   Fundamentals, Technicals, and News, including the Historical quote/bars breakdown; the panel
   does not render this report and the utility bar briefly flashes `Ingest finished.`
4. Force news: `docker compose exec worker curl -fsS -X POST 'http://api:8080/refresh/market?force_news=1'`
5. `docker compose exec worker curl -fsS 'http://api:8080/context/preview?book=portfolio'`
   and `?book=tracker` return book-scoped Historical, Fundamentals, Technicals and News snapshots

### Agent advisory (`behaviour/agent-advisory.md`, `0009`)

1. Set Claude OAuth + TypeSafe keys; rebuild worker if needed
2. UI → **Run now** — recommendations: portfolio buy/sell/hold/watch × **6m / 12m / 24m**; tracker × **1m / 3m / 6m**
3. Or run inside Compose: `docker compose exec worker curl -fsS -X POST http://api:8080/agent/run`, then `docker compose exec worker curl -fsS http://api:8080/agent/runs/latest`
4. Missing tokens → **failed** run with a visible error (not a silent no-op)
5. Repeats within 24h reuse SQLite cache; **Force run** or `?force=1` bypasses it
6. Recommendation chips = Jev **combined** lens; supporting lenses (historical · fundamentals · technicals ·
   news) under each ticker
7. **Log** on a ticker → Claude↔Jev transcript; latest run exposes **info-needs**
8. A second **Force run** re-decides everything; a scheduled or non-forced run after the cache window
   carries unchanged subjects — their tickers read "unchanged — carried from run #N", the log page names the
   deciding run, the run log lists `materiality <SYM>: …` per subject, and no new alerts appear for them
9. The run's model refs show `token_budget` with Claude CLI usage (`claude_research`, `claude_explain`) and Jev
   request characters per lens
10. Each panel shows a **Market read**; each ticker shows Claude's explanation (plus a "Tension:" line when
    given); `/portfolio/log/<symbol>` opens with a **Why** section and the transcript ends with an
    `explanation` turn (`0028`)
11. **Advisory logs** lists the newest 20 runs and loads metadata, `context …`, `materiality …`,
    and errors only after selection; the recommendation panel shows none of this operational text.

### Operation history and feedback (`0029`)

1. Successful and failed manual ingests each create a row with both timestamps and detail; a 21st
   row prunes the oldest, while scheduled ingestion creates none.
2. Advisory history includes scheduled, manual, forced, and targeted effective triggers and returns
   only 20 lightweight summaries without stored research or context.
3. Portfolio and Tracker open the same separate Advisory/Ingest modals, newest first. Verify loading,
   empty and API-error states, focus containment/restoration, Escape/Close, and narrow layout.
4. Global and Tracker-symbol runs show the exact concise success, warning, cached, timeout, or failure
   flash in the utility bar. Success dismisses at four seconds and warning/error at eight.
5. Operation messages never appear in either module panel or ticker log page; CRUD and loading
   feedback remains in place.

Token thrift defaults: `ADVISORY_INTERVAL_SECONDS=86400`, `ADVISORY_MAX_SCENARIO_ROUNDS=0` (set `3` for full
scenario loop per `0009`), `ADVISORY_MATERIAL_MOVE_PCT=5`, `ADVISORY_MAX_CARRY_DAYS=7` (`0` disables carrying).

### Alerts + schedule (`0011`, `0018`)

1. `/setup` — compact Service status reports required and optional readiness without exposing
   variable names, commands, vendor URLs, schedule details, or alert-policy prose
2. Buy/sell recommendations raise in-app alerts; bell → Unread / Read; **Ack** clears unread
3. `docker compose exec worker curl -fsS http://api:8080/alerts` and `docker compose exec worker curl -fsS -X POST http://api:8080/alerts/1/ack`; alert JSON includes `book`
4. Worker logs show schedule arming; after US close + `ADVISORY_AFTER_US_CLOSE_MINUTES` (default 30 → 16:30 ET) a `trigger=schedule` run starts at most once per ET trading day

### Profiles + doctrine (`0012`–`0018`)

1. `/setup` → save Portfolio profile + cash / realised gains with **Save portfolio**; the Investor
   profile remains unchanged
2. Save Investor profile with **Save investor profile**; Portfolio fields remain unchanged
3. Each form shows local loading/disabled, success, and error states; an empty Investor profile
   shows its generic-buying-lens warning; calculated realised total still renders
4. Forced advisory run — every recommendation has a `reason`; sells may show an informational `loss_gate`
5. Per-ticker log shows reason/gate as informational chips (nothing is suppressed in code)
6. `docker compose exec worker curl -fsS http://api:8080/agent/outcomes` — matured recs vs later prices (`pending` until horizons elapse)

### Display currency (`0025`)

1. `/setup` defaults to EUR and rejects codes outside EUR/USD/GBP/CHF.
2. Set source-aware cash and realised gains, then change EUR → USD using the independent currency form; both source currencies remain unchanged and their converted amounts update.
3. Portfolio and Tracker labels, totals, tables, calculations, and context previews use USD; native quotes/acquisitions stay native.
4. Create a new advisory run and confirm `display_currency: USD` plus generic `_display` context/prompt keys.
5. Open an older EUR run and confirm legacy `_eur` snapshot fields still render as EUR.
6. Remove a required cached rate in a scratch database: affected values say **pending FX**, totals/weights are unavailable, and no native/EUR amount is labelled USD.

### Setup service readiness

1. Required services present → Claude research and Jev decisions show **Ready**
2. Required services absent → their rows show **Needs attention**
3. Finnhub, Marketaux, and Alpha Vantage present → **Connected**; absent → **Not connected — optional**
4. Anthropic API billing configured → Claude research shows **Needs attention** and a concise
   billing warning; no environment variable name or remediation command appears
5. **Check again** refreshes service state and exposes a local checking/disabled state

### Tracker (`behaviour/tracker.md`, `0019`, `0020`)

1. `/tracker` → search (e.g. "airbus") → add the name without an operator note
2. **Ingest now** — quote / bars / fundamentals / technicals / news for the tracked name
3. **Force run** — Tracker recommendations, five lenses, horizons **1m / 3m / 6m**
4. `/tracker/log/<symbol>` — "Tracked, not owned"; investor profile as mandate context
5. Record an acquisition → entry archives to Portfolio; delete the holding → tracker returns
6. Adding a symbol that is already a holding → HTTP 409
7. Tracker displays Daily % from quote versus prior-session close with green, red, or neutral
   background, and exposes Run/Remove as labelled icon controls

## Automated (worker)

Run inside Compose after `bin/up -d`:

```bash
docker compose exec api php test_holding_daily_change.php
docker compose exec api php test_currency.php
docker compose exec api php test_history.php
docker compose exec api php test_operation_history.php
docker compose exec worker python test_doctrine.py
docker compose exec worker python test_currency.py
docker compose exec worker python test_fundamentals_adapters.py
docker compose exec worker python test_ingestion.py
docker compose exec worker python test_persist_e2e.py
docker compose exec worker python test_digest.py
docker compose exec worker python test_materiality.py
docker compose exec worker python test_advisory_budget.py
docker compose exec worker python test_explain.py
```

| Script | Covers |
| --- | --- |
| `test_holding_daily_change.php` | Portfolio and Tracker live quote versus prior-session close, including unavailable values |
| `test_currency.php` | EUR default, validation, partial updates, legacy migration, source currencies, cross-rates, quote-currency conversion and missing FX |
| `test_history.php` | Ranges, common-date normalization, pre-benchmark Max points, native currencies, missing data and validation |
| `test_operation_history.php` | Manual ingest success/failure detail and newest-20 pruning |
| `test_doctrine.py` | Choice parsing, loss-gate labels, mandate composition, tracker vs portfolio choice maps |
| `worker/test_currency.py` | Worker FX collection and display-currency advisory context, including incomplete aggregate behavior |
| `test_fundamentals_adapters.py` | Finnhub/Alpha Vantage normalization, failures, symbol translation, routing and persisted daily quota |
| `test_ingestion.py` | Completeness/scoring, ordered fallbacks, cache protection, persistent rate state, cadence isolation, unchanged-bar behavior |
| `test_persist_e2e.py` | Persisted action is exactly what Jev decided (never rewritten); tracker verbs, `book` column, five-lens rule; carried rows copy the decision and explanation, name the deciding run, and never alert; a missing explanation never blocks persistence |
| `test_digest.py` | Layer cards: rounding, whitelists, ETF trimming, news dedupe, historical statistics from long-history points and benchmark (never the points) |
| `test_materiality.py` | Every re-decide trigger, carry when nothing is material, tracker ignores holding-only triggers, forced/targeted runs, carry-days cap |
| `test_explain.py` | Explain prompt carries combined choice, confidence and probabilities, lens verdicts, market cards and headlines (never history points); bare structured call; tolerant parsing; a Claude failure is logged, never raised |
| `test_advisory_budget.py` | 14-subject worst case stays under Jev and Claude payload ceilings; guidance sent once per call; each lens sees only its own layer; combined sees the lens verdicts |

## Browser acceptance (Docker Playwright MCP)

1. `/` redirects to `/portfolio`; `/?buy=NVDA` redirects to `/portfolio?buy=NVDA` and opens the acquisition form.
2. Portfolio and Tracker use different palettes, page composition and language; switching modules updates `aria-current` and browser history.
3. The neutral utility bar stays available in both modules and labels its operations as applying to both books.
4. `/portfolio/log/<symbol>` and `/tracker/log/<symbol>` keep the correct module identity. Legacy `/log/<symbol>` redirects to the latest recommendation's book.
5. The global alert menu labels each alert `portfolio` or `tracker`; its symbol link opens the corresponding module log and Ack still works.
6. At desktop and narrow viewports, tables scroll instead of clipping, the utility bar wraps/stacks, focus is visible and all controls are keyboard reachable.
7. Capture screenshots of both modules at desktop and narrow widths for stakeholder visual review.
8. Both modules show an Agent context preview after recommendations. Every ticker is closed by
   default; opening one reveals three columns (Historical, Fundamentals, Technicals) and a News row.
9. `/setup` is neutral with side-by-side Portfolio then Tracker cards at desktop width and a compact
   display-currency row after them; at a narrow viewport the cards and currency row stack without
   clipped fields or losing the cards' evergreen/cobalt identities.
10. Setup forms and **Check again** are keyboard reachable, focus is visible, labels are explicit,
    and card/status text remains readable at WCAG AA contrast.
11. Each book row has a labelled chart toggle with `aria-expanded`; 5Y loads by default, range
    changes update the shared SVG, and opening another row closes the first.

## Full happy path (release)

1. `bin/up -d --build`
2. Configure keys on `/setup` (values in `.env`)
3. Write investor + portfolio profiles; set cash if desired
4. Add at least one holding and one tracker name
5. **Ingest now** → configured-currency values / technicals / fundamentals and both-book news when configured
6. **Force run** → recommendations in both modules; alerts for buy/sell; book-scoped log transcript opens
7. Both worker test scripts pass
8. `bin/down` / `bin/up -d` — book and run history still present

## Out of scope for this checklist

IBKR production wiring, rich fundamentals, email/push, backtesting, broker sync, open-universe screening, holdings-form instrument autofill (`PROJECT.md` Later).
