# Tradai

Personal agentic trading dashboard for a solo operator: monitor EU and US listed equities and ETFs, keep a second **tracker** book of names you are considering, and get daily AI advisory (Claude + Jev) after the US close. Advisory only — Tradai never places trades.

Spec-driven development: product intent lives under [`spec/`](spec/); agents should read [`AGENTS.md`](AGENTS.md).

**Not investment advice.** For personal use on your own machine / LAN. Portfolio data stays local; configured Claude/Jev providers receive the full context needed for advisory.

## Requirements

- Docker and Docker Compose
- Optional API keys (see below) depending on which features you use

## Quick start

```bash
cp .env.example .env
# Edit .env — at minimum leave it empty to try Compose + portfolio CRUD

bin/up -d
```

Then open **http://127.0.0.1:3000**.

| Script | Purpose |
| --- | --- |
| `bin/up` | `docker compose up --build` (extra args forwarded, e.g. `bin/up -d`) |
| `bin/down` | `docker compose down` (add `-v` only if you want to wipe the SQLite volume) |

Local ports (bound to localhost):

| Service | URL |
| --- | --- |
| UI | http://127.0.0.1:3000 |
| API | http://127.0.0.1:8080 |

Stop with `bin/down`. Data survives restarts on the `tradai-data` volume unless you pass `-v`.

## Configuration

Copy `.env.example` to `.env`. Secrets stay on the host; the Setup page only shows whether keys are present.

| Variable | Used for |
| --- | --- |
| `FINNHUB_API_KEY` | US quotes (https://finnhub.io — free personal key) |
| `MARKETAUX_API_TOKEN` | News (https://www.marketaux.com — free tier OK) |
| `CLAUDE_CODE_OAUTH_TOKEN` | Claude Agent CLI in Docker — on the host run `claude setup-token` |
| `TYPESAFE_API_KEY` | Jev decisions (https://console.typesafe.ai) |
| `TYPESAFE_MODEL` | Defaults to `jev-latest` |
| `ADVISORY_INTERVAL_SECONDS` | Default `86400` — one successful/partial advisory per day (SQLite cache) |
| `ADVISORY_MAX_SCENARIO_ROUNDS` | Default `0` — skip extra Claude↔Jev rounds; set `3` for the full loop |
| `ADVISORY_AFTER_US_CLOSE_MINUTES` | Default `30` → fire at 16:30 America/New_York |
| `ADVISORY_SCHEDULE_CHECK_SECONDS` | How often the worker checks the schedule window |
| `FMP_API_KEY` | Optional; surfaced for future fundamentals — not consumed today |
| `IBKR_ENABLED` | Optional IBKR quotes stub (`0` by default) |

**Do not set `ANTHROPIC_API_KEY`.** It takes precedence and switches Claude billing to pay-as-you-go.

EU listings generally work via yfinance without Finnhub. Rebuild after changing worker-related env: `bin/up -d --build`.

## First run

1. Open **Setup** (`/setup`) — confirm which keys are loaded; write optional **Investor** and **Portfolio** profiles and cash / realised-gains fields.
2. **Portfolio** (`/portfolio`) — add holdings as acquisitions (symbol, venue, quantity, unit price, commission, trade date).
3. **Tracker** (`/tracker`) — search by name or ticker and add names you are watching (with a note).
4. **Ingest now** — quotes, bars, technicals (and holdings news when Marketaux is set).
5. **Run now** / **Force run** — recommendations in both modules; buy/sell rows raise in-app alerts (bell, top right).

Portfolio horizons are **6 / 12 / 24 months**; tracker horizons are **1 / 3 / 6 months**. The daily schedule runs after US regular close plus your offset (default 16:30 ET), at most once per ET trading day.

## Useful API (local)

The Slim API is at `127.0.0.1:8080`. Common calls:

```bash
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/holdings
curl -s -X POST 'http://127.0.0.1:8080/agent/run?force=1'
curl -s http://127.0.0.1:8080/agent/runs/latest
curl -s http://127.0.0.1:8080/alerts
```

The worker HTTP port is internal to Compose only.

## Verify / regression

Acceptance checks and worker tests: [`spec/tests.md`](spec/tests.md).

```bash
docker compose exec worker python test_doctrine.py
docker compose exec worker python test_persist_e2e.py
```

## Specs

| Path | Role |
| --- | --- |
| [`spec/PROJECT.md`](spec/PROJECT.md) | Goals, non-goals, happy path |
| [`spec/`](spec/) | Domain, architecture, data, behaviour, decisions |
| [`AGENTS.md`](AGENTS.md) | Precedence for automated agents |

## License / posture

Personal prototype. Respect each market-data vendor’s terms. Do not expose the stack to the public internet.
