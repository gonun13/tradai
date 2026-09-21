# Tradai

Personal agentic trading dashboard. Spec-driven — see `spec/` and `AGENTS.md`.

## MVP stages

**MVP closed** (stages 1–6 + 5b). Checklist and notes: [`spec/stages.md`](spec/stages.md). Verify steps below remain useful for regression.

**Stage 7 — investor profiles** (`spec/decisions/0012`–`0019`). Load separate investor and portfolio
profiles on the Setup page; Claude and Jev read the applicable profile(s) and the sell-doctrine guidance
directly and decide — nothing in the worker overrides them (`0018`, `0019`).

## Stage 1 — verify

```bash
bin/up
```

Then:

1. `curl -s http://127.0.0.1:8080/health` — JSON with `"ok": true`
2. Open `http://127.0.0.1:3000` — the **Tradai** portfolio dashboard loads
3. `docker compose logs worker --tail 20` — periodic `[tradai-worker] heartbeat`
4. `bin/down` then `bin/up` — worker heartbeat file still under the `tradai-data` volume (use `bin/down -v` only if you want to wipe data)

Optional: `cp .env.example .env`

### Scripts

| Script | Purpose |
| --- | --- |
| `bin/up` | `docker compose up --build` (extra args forwarded, e.g. `bin/up -d`) |
| `bin/down` | `docker compose down` (add `-v` to drop volumes) |

### API surface

The local Slim API is bound to `127.0.0.1:8080`. In addition to the verification endpoints
used below, it exposes:

- `GET|PUT /portfolio/settings` — read or save the investor/portfolio profiles, cash, and realised-gains override
- `GET|POST /theses`, `PUT|DELETE /theses/{id}` — thesis records and coverage
- `GET|POST /tracker`, `PUT|DELETE /tracker/{id}` — tracker entries
- `GET /instruments/search?q=...` — local/Yahoo/Finnhub instrument search

The worker listens only inside Compose on port 8090. Its internal routes are `GET /health`,
`GET /search?q=...`, `POST /refresh` (also `/ingest`), and `POST /advisory/run` (also `/agent/run`).

## Stage 2 — verify

1. `bin/up -d`
2. Open `http://127.0.0.1:3000` — add / edit / delete holdings
3. `curl -s http://127.0.0.1:8080/holdings` — JSON book
4. `bin/down` then `bin/up -d` — holdings still present (unless you used `-v`)

## Stage 3 — verify

1. `cp .env.example .env` and set `FINNHUB_API_KEY` (US quotes)
2. `bin/up -d`
3. Open UI → **Ingest now** (or Refresh quotes on older UI)
4. EUR cost / market value / P&L fill in; EU names work without Finnhub via yfinance

## Stage 4 — verify

1. Set `MARKETAUX_API_TOKEN` in `.env` (https://www.marketaux.com — free tier OK)
2. `bin/up -d` (rebuild worker if already running)
3. UI → **Ingest now** — news is fetched at most once per day (SQLite cache); quotes/technicals still refresh
4. Holdings show technicals (RSI/SMA); news appears when the token is set
5. Force a news re-pull: `curl -s -X POST 'http://127.0.0.1:8080/refresh/market?force_news=1'`
6. Optional: `curl -s http://127.0.0.1:8080/context/preview`

## Stage 5 — verify

1. On the host: `claude setup-token` → put the value in `.env` as `CLAUDE_CODE_OAUTH_TOKEN`
2. Create a TypeSafe key at https://console.typesafe.ai → `TYPESAFE_API_KEY` in `.env`
3. **Do not** set `ANTHROPIC_API_KEY`
4. `bin/up -d` (worker rebuild installs Claude CLI)
5. UI → **Run now** — recommendations panel shows portfolio buy/sell/hold/watch × 6m/12m/24m
   and tracker entry decisions × 1m/3m/6m
6. Or: `curl -s -X POST http://127.0.0.1:8080/agent/run` then `curl -s http://127.0.0.1:8080/agent/runs/latest`
7. With tokens missing, a **failed** run should still appear with an error message

## Stage 5b — verify

Same tokens as Stage 5. Rebuild worker/api after pull.

1. `bin/up -d`
2. UI → **Run now** — first call runs Claude+Jev; repeats within 24h reuse SQLite cache (no new tokens)
3. Recommendation chips = Jev **combined** lens; supporting thesis/news/technicals under each ticker
4. Click **log** on a ticker — Claude↔Jev transcript
5. Latest run shows **info-needs** for ingest planning
6. **Force run** (or `curl -s -X POST 'http://127.0.0.1:8080/agent/run?force=1'`) bypasses the daily cache

Token thrift defaults (`.env`):

- `ADVISORY_INTERVAL_SECONDS=86400` — one successful/partial advisory per day
- `ADVISORY_MAX_SCENARIO_ROUNDS=0` — skip extra Claude↔Jev scenario rounds (set `3` for full `0009` loop)

## Stage 6 — verify

1. `cp .env.example .env` and set Finnhub / Marketaux / `CLAUDE_CODE_OAUTH_TOKEN` / `TYPESAFE_API_KEY` as needed
2. Optional: `ADVISORY_AFTER_US_CLOSE_MINUTES=30` (fire at 16:30 America/New_York)
3. `bin/up -d`
4. Open `http://127.0.0.1:3000/setup` — key presence + schedule docs (secrets stay in `.env`)
5. Dashboard → **Run now** / **Force run** — buy/sell recommendations raise alerts; bell (top right) → **Unread** / **Read**; **Ack** clears unread
6. Or: `curl -s http://127.0.0.1:8080/alerts` and `curl -s -X POST http://127.0.0.1:8080/alerts/1/ack`
7. Worker logs show schedule arming; after US close+offset on a trading day it starts `trigger=schedule` (at most once per ET day)

## Stage 7 — verify

Gives Claude and Jev investor and portfolio profiles to reason with (`spec/decisions/0012`–`0019`).

Background: Claude's entire brief used to be *"Tradai RESEARCHER … Be concise"* and Jev was asked for
"the best overall advisory action" with *best* defined nowhere. An unanchored model shown a negative
P&L recommends selling. There are now separate investor and portfolio profile fields, richer context (thesis, held days, cost weight,
cash, realised gains), and prompts that ask Jev to name a reason — but no code-level gate. Claude and
Jev are the intelligence; the worker just labels their answer for the log.

1. `bin/up -d --build` (schema migrates on API boot; existing recommendations are kept)
2. Open `http://127.0.0.1:3000/setup` → **Investor profile** and **Portfolio profile** — write the
   applicable guidance, set a cash reserve, and save. Both are optional; sensible defaults apply if unset.
3. `curl -s -X POST 'http://127.0.0.1:8080/agent/run?force=1'` — portfolio horizons are **6m / 12m / 24m**;
   tracker horizons are **1m / 3m / 6m**
4. `curl -s http://127.0.0.1:8080/agent/runs/latest` — every recommendation carries a `reason` (parsed
   from Jev's answer) and, for sells, an informational `loss_gate` label
5. `docker compose exec worker python test_doctrine.py` — the two pure helpers (choice parsing, label)
6. `docker compose exec worker python test_persist_e2e.py` — confirms the action written is always
   exactly what Jev decided, never rewritten, through the real persist path against a scratch DB copy
7. Per-ticker **log** page shows the reason/gate as an informational chip — not an audit trail, since
   nothing is suppressed
8. `curl -s http://127.0.0.1:8080/agent/outcomes` — scores matured recommendations against subsequent
   prices. Expect `pending` for the first six months.
