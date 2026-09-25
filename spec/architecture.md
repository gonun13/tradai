# Architecture

Audience: engineering. Structure, boundaries, and constraints for a solo, local Docker, ~1-week prototype.

## Topology

```
  [Browser]
      |
      v
  [Nuxt 4 UI]  ----HTTP---->  [Slim PHP API]  <---->  [SQLite volume]
                                   ^
                                   | job status / results
                                   v
                          [Python agent worker]
                           |         |        |
                     market APIs   news   Claude Agent CLI
                           |         |    (subscription auth)
                           |         |        + Jev
                      (public data) (public) (full portfolio
                                             context for
                                             decisions)
```

## Components

| Piece | Role |
| --- | --- |
| **Nuxt 4** | Dashboard: portfolio, monitoring, recommendations, alerts, setup screens. |
| **Slim PHP API** | Local HTTP API: holdings CRUD, read models for dashboard, alert ack, trigger “run now”. Owns portfolio writes. |
| **SQLite** | Single-node state on a Docker volume: portfolio, cached bars, agent runs, recommendations, alerts. |
| **Python worker** | Scheduled ingestion + analysis. Computes technicals locally; calls market/news APIs; invokes Claude Agent CLI + Jev with **full portfolio context** for decisions. Writes results via API or shared DB. |
| **Scheduler** | Worker loop with persisted per-instrument due state — quotes 15m, bars/news daily, adjusted long history weekly, complete fundamentals 7d and missing/partial fundamentals daily, technicals on bar change; **agent advisory once per day after US markets close**. |

## Client vs server vs background

- **Client:** Nuxt pages; prefer server-side keys in Compose env — no secrets in the browser beyond what is unavoidable for local use.
- **Server:** Slim PHP — sync request/response; portfolio authority.
- **Background:** Python worker — long-running / scheduled; no public ingress.

## Stack (locked for v1)

| Choice | Notes |
| --- | --- |
| Nuxt 4 + TypeScript, Node 22 | UI / Nitro `node-server` in Docker |
| Slim PHP 4 (PHP 8.x) | Thin local HTTP API |
| Python agent worker | Ingestion, technicals, Claude Agent CLI / Agent SDK + Jev orchestration |
| Claude Agent CLI (subscription auth) + Jev | No `ANTHROPIC_API_KEY` in worker env (it wins precedence and switches to API billing) |
| SQLite on named Docker volume | All durable state |
| Docker Compose | UI, API, worker, volume, scheduler |
| Market data | Four outcome-oriented registries (`0022`): Historical, Fundamentals, Technicals, and News. Each operation selects one complete provider response or one best partial without merging. Frankfurter FX is auxiliary. See `source-matrix-v1.md`. |
| News | Marketaux (US + EU); Finnhub US news as backup |
| Technicals | Local compute (e.g. pandas-ta or equivalent) from OHLCV |

## Interfaces (coarse)

- **Browser → Slim:** holdings CRUD, dashboard read models, lazy range-based performance history,
  alert acknowledge, manual “run now.”
- **Worker → market/news/FX APIs:** ordered per-operation adapter registries for quotes, bars, headlines, and light fundamentals; local technicals; independent Frankfurter base→EUR pivot rates used for configured display conversion (`0025`).
- **Worker → Claude Agent CLI / Jev:** orchestrated **researcher/decider** loop with full portfolio context — Claude builds lensed Jev requests and may iterate scenarios; Jev returns typed decisions (`0009`). Not a one-shot rationale→decision call.
- **Worker → Slim or shared SQLite:** persist AgentRun (incl. research + info-needs), Recommendation (combined action, supporting lenses, conversation), Alert, cached bars, and separately compacted adjusted long history.

The implemented browser surface is Slim as the single BFF: holdings, tracker, settings, theses,
search, ingest, advisory runs, recommendations, alerts, and outcomes are exposed through Slim.
The worker's refresh, advisory, search, and health routes remain internal to Compose.

The Nuxt surface has separate `/portfolio` and `/tracker` modules (`0021`) over those shared
interfaces. A neutral utility bar owns combined ingest/advisory controls and global alerts;
module-specific pages and logs supply the distinct book context.

## Privacy and security constraints

- Portfolio data stored LAN-only; no cloud DB.
- Bind services to localhost or home LAN, not the public internet.
- Secrets in Compose env / local `.env`, not committed.
- Claude auth via subscription OAuth only (`claude auth login` or `CLAUDE_CODE_OAUTH_TOKEN`); never set `ANTHROPIC_API_KEY` for this project’s intended billing path.
- External LLM/decision calls **may and should include full holdings** for advisory quality (see `domain.md` / `data.md`). This is an accepted personal-use tradeoff.

## Non-functional requirements

| Area | Requirement |
| --- | --- |
| Scale | Single user; dozens–low hundreds of instruments — not market-wide scanning |
| Availability | Best-effort while the home machine is on; no SLA |
| Latency | Quote refresh: seconds–minutes (delayed OK). Agent runs: minutes, async |
| Offline | Not required; a fresher or more complete cached dataset is never overwritten by a worse provider response |
| Chart history | Weekly, chart-only adjusted series; benchmark failure is isolated from instrument ingestion and the UI |
| i18n | Not required for MVP |

## Assumptions carried from design

1. Python worker + Slim API split (Slim owns portfolio HTTP; Python owns schedules and agent orchestration).
2. SQLite for all durable state in v1.
3. Agent runtime = Claude Agent CLI on subscription auth — **researcher** role; not Anthropic Managed Agents, not API-key billing.
4. Jev = TypeSafe System One for structured advisory **decisions** (lenses + combined canonical action); see `0009`.
5. Market-data = capability × region adapters with locked free defaults and optional IBKR quotes (`0006`).
6. Alerts = in-app first.
7. “Live” = polling delayed / near-live quotes.
8. No auth system beyond machine / LAN trust for v1.

## Open (architecture)

- Notifications beyond the in-app alert surface and the exact day-one MIC set remain open; Nuxt uses
  Slim as its BFF and the worker HTTP interface is internal.

Claude-in-Docker auth is locked in `decisions/0008-claude-docker-oauth-token.md`.
Claude researcher / Jev decider roles are locked in `decisions/0009-claude-researcher-jev-decider.md`.
Alert policy is locked in `decisions/0018-simplify-trust-agents.md` (revising `0010`/`0017`).
Post–US-close schedule offset is locked in `decisions/0011-us-close-schedule-offset.md`.
