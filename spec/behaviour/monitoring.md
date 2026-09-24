# Behaviour — Monitoring

Near-live dashboard view of holdings with market context. Polling delayed or near-live quotes — not websocket co-location.

## Goals

- At a glance: holdings, quotes, and recent agent output.
- Refresh quotes on a schedule aware of relevant EU and US market hours without operator babysitting.
- Degrade gracefully when vendors flake (cached bars / last quote still render).

## Flows

### Quote refresh (scheduled)

1. Scheduler triggers the Python worker (or a dedicated ingest path) during relevant market hours (EU and/or US sessions as needed for the book).
2. Worker fetches quotes / bars for instruments in **either book** — holdings and unarchived tracked names
   (`0019`) — via **Historical** adapters (region-routed per `0006`; optional IBKR quotes when session available).
3. Results land in SQLite (and/or via Slim).
4. Nuxt read models show updated figures within seconds–minutes latency expectations.

### Dashboard glance

1. Operator opens the UI.
2. Sees holdings + latest available quotes (**money in EUR**), and in the Tracker module the same for names
   being considered — price, 1m/3m momentum and RSI, with no cost basis to show.
3. Sees recent recommendations / alerts summary without opening a separate “pro terminal.”

### News context (lightweight)

- Worker may cache NewsItems tagged to instruments for agent and UI context.
- News is supportive context, not a full news product.
- **Holdings only** (`0019`). The Marketaux free tier is 100 requests/day, and it is spent on the book the
  operator owns. Tracked names are therefore omitted from the agents' `news` lens rather than judged blind.

## Rules

- “Live” = polling; delayed data is acceptable for MVP.
- Do not scan the whole market — only instruments relevant to the two books (dozens–low hundreds). Open-universe
  screening remains deferred (`0016`): the tracker universe is operator-curated.
- Use pluggable capability × region adapters per `decisions/0006-market-data-adapters.md`; degrade to cache when a vendor or IBKR session is down.

## Acceptance cues

- With network + keys configured, quotes appear or update after a scheduled tick.
- With APIs down, UI still shows last cached bars/quotes rather than a blank portfolio.

## Out of scope here

- Full charting suites, Level 2, options chains, complex scanners.
- Email/push notification channels (alerts behaviour is separate; monitoring is the glance surface).
