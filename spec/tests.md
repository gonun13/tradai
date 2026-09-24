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
4. Money labels on the dashboard are EUR

### Quotes, bars, FX (`0006`)

1. Set Finnhub for US names; EU names (e.g. `AM.PA`) work via yfinance without a key
2. UI → **Ingest now** (or Refresh quotes)
3. EUR cost / market value / P&L fill in; last cache still shows if a vendor errors

**Note:** Finnhub free plans often block `/stock/candle`; US quotes may come from Finnhub with bars falling back to yfinance.

### News + technicals

1. Set `MARKETAUX_API_TOKEN`
2. **Ingest now** — technicals (RSI/SMA), fundamentals when a route is available, and
   daily-cached news on holdings and tracked names
3. The completion message reports ingested vs cached instrument datasets for Historical,
   Fundamentals, Technicals, and News; `/ingest/report` retains the same statistics plus
   missing counts and the Historical quote/bars breakdown
4. Force news: `docker compose exec worker curl -fsS -X POST 'http://api:8080/refresh/market?force_news=1'`
5. `docker compose exec worker curl -fsS 'http://api:8080/context/preview?book=portfolio'`
   and `?book=tracker` return book-scoped Historical, Fundamentals, Technicals and News snapshots

### Agent advisory (`behaviour/agent-advisory.md`, `0009`)

1. Set Claude OAuth + TypeSafe keys; rebuild worker if needed
2. UI → **Run now** — recommendations: portfolio buy/sell/hold/watch × **6m / 12m / 24m**; tracker × **1m / 3m / 6m**
3. Or run inside Compose: `docker compose exec worker curl -fsS -X POST http://api:8080/agent/run`, then `docker compose exec worker curl -fsS http://api:8080/agent/runs/latest`
4. Missing tokens → **failed** run with a visible error (not a silent no-op)
5. Repeats within 24h reuse SQLite cache; **Force run** or `?force=1` bypasses it
6. Recommendation chips = Jev **combined** lens; supporting lenses under each ticker
7. **Log** on a ticker → Claude↔Jev transcript; latest run exposes **info-needs**

Token thrift defaults: `ADVISORY_INTERVAL_SECONDS=86400`, `ADVISORY_MAX_SCENARIO_ROUNDS=0` (set `3` for full scenario loop per `0009`).

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

### Setup service readiness

1. Required services present → Claude research and Jev decisions show **Ready**
2. Required services absent → their rows show **Needs attention**
3. Finnhub, Marketaux, and FMP present → **Connected**; absent → **Not connected — optional**
4. Anthropic API billing configured → Claude research shows **Needs attention** and a concise
   billing warning; no environment variable name or remediation command appears
5. **Check again** refreshes service state and exposes a local checking/disabled state

### Tracker (`behaviour/tracker.md`, `0019`, `0020`)

1. `/tracker` → search (e.g. "airbus") → add with a note
2. **Ingest now** — quote / bars / fundamentals / technicals / news for the tracked name
3. **Force run** — Tracker recommendations, four lenses, horizons **1m / 3m / 6m**
4. `/tracker/log/<symbol>` — "Tracked, not owned"; investor profile as mandate context
5. Record an acquisition → entry archives to Portfolio; delete the holding → tracker returns with note intact
6. Adding a symbol that is already a holding → HTTP 409

## Automated (worker)

Run inside Compose after `bin/up -d`:

```bash
docker compose exec worker python test_doctrine.py
docker compose exec worker python test_ingestion.py
docker compose exec worker python test_persist_e2e.py
```

| Script | Covers |
| --- | --- |
| `test_doctrine.py` | Choice parsing, loss-gate labels, mandate composition, tracker vs portfolio choice maps |
| `test_ingestion.py` | Completeness/scoring, ordered fallbacks, cache protection, persistent rate state, cadence isolation, unchanged-bar behavior |
| `test_persist_e2e.py` | Persisted action is exactly what Jev decided (never rewritten); tracker verbs, `book` column, four-lens rule |

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
9. `/setup` is neutral with side-by-side Portfolio then Tracker cards at desktop width; at a narrow
   viewport the cards stack without clipped fields or losing their evergreen/cobalt identities.
10. Setup forms and **Check again** are keyboard reachable, focus is visible, labels are explicit,
    and card/status text remains readable at WCAG AA contrast.

## Full happy path (release)

1. `bin/up -d --build`
2. Configure keys on `/setup` (values in `.env`)
3. Write investor + portfolio profiles; set cash if desired
4. Add at least one holding and one tracker name
5. **Ingest now** → EUR quotes / technicals / fundamentals and both-book news when configured
6. **Force run** → recommendations in both modules; alerts for buy/sell; book-scoped log transcript opens
7. Both worker test scripts pass
8. `bin/down` / `bin/up -d` — book and run history still present

## Out of scope for this checklist

IBKR production wiring, rich fundamentals, email/push, backtesting, broker sync, open-universe screening, holdings-form instrument autofill (`PROJECT.md` Later).
