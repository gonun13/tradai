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

1. `bin/up -d`
2. `curl -s http://127.0.0.1:8080/health` → JSON with `"ok": true`
3. Open `http://127.0.0.1:3000` — Portfolio dashboard loads
4. `docker compose logs worker --tail 20` — periodic `[tradai-worker] heartbeat`
5. `bin/down` then `bin/up -d` — data under `tradai-data` still present (unless you used `-v`)

## Acceptance by area

### Portfolio (`behaviour/portfolio.md`)

1. UI: add / edit / delete a holding (acquisition: trade date, qty, unit price, commission)
2. `curl -s http://127.0.0.1:8080/holdings` — book JSON
3. Restart Compose — holding still present
4. Money labels on the dashboard are EUR

### Quotes, bars, FX (`0006`)

1. Set Finnhub for US names; EU names (e.g. `AM.PA`) work via yfinance without a key
2. UI → **Ingest now** (or Refresh quotes)
3. EUR cost / market value / P&L fill in; last cache still shows if a vendor errors

**Note:** Finnhub free plans often block `/stock/candle`; US quotes may come from Finnhub with bars falling back to yfinance.

### News + technicals

1. Set `MARKETAUX_API_TOKEN`
2. **Ingest now** — technicals (RSI/SMA) on holdings; news when the token is set (news at most once per day from SQLite cache)
3. Force news: `curl -s -X POST 'http://127.0.0.1:8080/refresh/market?force_news=1'`
4. Optional: `curl -s http://127.0.0.1:8080/context/preview`

### Agent advisory (`behaviour/agent-advisory.md`, `0009`)

1. Set Claude OAuth + TypeSafe keys; rebuild worker if needed
2. UI → **Run now** — recommendations: portfolio buy/sell/hold/watch × **6m / 12m / 24m**; tracker × **1m / 3m / 6m**
3. Or: `curl -s -X POST http://127.0.0.1:8080/agent/run` then `curl -s http://127.0.0.1:8080/agent/runs/latest`
4. Missing tokens → **failed** run with a visible error (not a silent no-op)
5. Repeats within 24h reuse SQLite cache; **Force run** or `?force=1` bypasses it
6. Recommendation chips = Jev **combined** lens; supporting lenses under each ticker
7. **Log** on a ticker → Claude↔Jev transcript; latest run exposes **info-needs**

Token thrift defaults: `ADVISORY_INTERVAL_SECONDS=86400`, `ADVISORY_MAX_SCENARIO_ROUNDS=0` (set `3` for full scenario loop per `0009`).

### Alerts + schedule (`0011`, `0018`)

1. `/setup` — key presence docs (secrets stay in `.env`)
2. Buy/sell recommendations raise in-app alerts; bell → Unread / Read; **Ack** clears unread
3. `curl -s http://127.0.0.1:8080/alerts` and `curl -s -X POST http://127.0.0.1:8080/alerts/1/ack`
4. Worker logs show schedule arming; after US close + `ADVISORY_AFTER_US_CLOSE_MINUTES` (default 30 → 16:30 ET) a `trigger=schedule` run starts at most once per ET trading day

### Profiles + doctrine (`0012`–`0018`)

1. `/setup` → Investor profile + Portfolio profile + cash / realised gains → save
2. Forced advisory run — every recommendation has a `reason`; sells may show an informational `loss_gate`
3. Per-ticker log shows reason/gate as informational chips (nothing is suppressed in code)
4. `curl -s http://127.0.0.1:8080/agent/outcomes` — matured recs vs later prices (`pending` until horizons elapse)

### Tracker (`behaviour/tracker.md`, `0019`, `0020`)

1. `/tracker` → search (e.g. "airbus") → add with a note
2. **Ingest now** — quote / bars / technicals for the tracked name; **no** news section for it
3. **Force run** — Tracker tab recommendations, three lenses (no news), horizons **1m / 3m / 6m**
4. `/log/<symbol>` — "Tracked, not owned"; investor profile as mandate context
5. Record an acquisition → entry archives to Portfolio; delete the holding → tracker returns with note intact
6. Adding a symbol that is already a holding → HTTP 409

## Automated (worker)

Run inside Compose after `bin/up -d`:

```bash
docker compose exec worker python test_doctrine.py
docker compose exec worker python test_persist_e2e.py
```

| Script | Covers |
| --- | --- |
| `test_doctrine.py` | Choice parsing, loss-gate labels, mandate composition, tracker vs portfolio choice maps |
| `test_persist_e2e.py` | Persisted action is exactly what Jev decided (never rewritten); tracker verbs, `book` column, three-lens rule |

## Full happy path (release)

1. `bin/up -d --build`
2. Configure keys on `/setup` (values in `.env`)
3. Write investor + portfolio profiles; set cash if desired
4. Add at least one holding and one tracker name
5. **Ingest now** → EUR quotes / technicals (and holdings news if token set)
6. **Force run** → recommendations on both tabs; alerts for buy/sell; log transcript opens
7. Both worker test scripts pass
8. `bin/down` / `bin/up -d` — book and run history still present

## Out of scope for this checklist

IBKR production wiring, rich fundamentals, email/push, backtesting, broker sync, open-universe screening, holdings-form instrument autofill (`PROJECT.md` Later).
