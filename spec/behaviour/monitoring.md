# Behaviour — Monitoring

Near-live dashboard view of holdings with market context. Polling delayed or near-live quotes — not websocket co-location.

## Goals

- At a glance: holdings, quotes, and recent agent output.
- Refresh quotes on a schedule aware of relevant EU and US market hours without operator babysitting.
- Degrade gracefully when vendors flake (cached bars / last quote still render).

## Flows

### Independently scheduled ingestion

1. The worker considers all holdings and unarchived tracked names, ordered by oldest
   successful result for the operation.
2. Quotes run every 15 minutes; daily bars and news daily; complete fundamentals every seven
   days, while missing or partial fundamentals retry daily. Technicals run only when the
   accepted bars change. FX has an independent cache covering instrument currencies, quote
   currencies, monetary-setting source currencies, and every supported display currency (`0025`).
3. Each requested dataset tries its ordered registry until one response is complete. Quote
   and bars fall back independently. A news or fundamentals snapshot always has one source.
4. If no complete response exists, the best one-source partial is accepted only when it
   improves the cache. A provider cooldown skips that provider without blocking other work.
5. Results land in SQLite and Nuxt read models show the latest accepted cache.

### Long-history chart refresh

1. Missing instrument history runs on the next scheduled or manual ingest; accepted history is
   thereafter due weekly.
2. Yahoo complete adjusted history is rebuilt into daily (latest year), last-of-ISO-week (years
   1–5), and last-of-calendar-month (older) points.
3. One SPY benchmark fetch serves all active US names and one EXSA.DE fetch serves all active
   European names.
4. An instrument response that loses either coverage edge does not replace cache. Benchmark
   failures are reported separately and do not block any instrument or layer.
5. Ingest statistics add `historical.operations.long_history`; benchmark refresh status is a
   separate report object rather than an instrument-dataset count.

### Dashboard glance

1. Operator opens the UI.
2. Sees holdings + latest available quotes (converted money in the configured display currency), and in the Tracker module the same for names
   being considered — price, 1m/3m momentum and RSI, with no cost basis to show.
3. Sees recent recommendations / alerts summary without opening a separate “pro terminal.”

### News context (lightweight)

- Worker may cache NewsItems tagged to instruments for agent and UI context.
- News is supportive context, not a full news product.
- Both books receive instrument-linked news (`0022`). Marketaux is primary; Finnhub is the
  US fallback after an empty or failed Marketaux response.

## Rules

- “Live” = polling; delayed data is acceptable for MVP.
- Do not scan the whole market — only instruments relevant to the two books (dozens–low hundreds). Open-universe
  screening remains deferred (`0016`): the tracker universe is operator-curated.
- Use ordered operation × region × kind registries per `decisions/0022-four-layer-ingestion.md`,
  with fundamentals revised by `decisions/0023-free-fundamentals-routing.md`.
- Never merge providers within one requested dataset. Preserve a fresher or more complete cache.
- Manual ingest refreshes quotes immediately, retries missing or partial fundamentals, and only
  runs the remaining layers when due. Complete fundamentals remain due-only.
  `force_news=1` bypasses freshness but not known hard quota/cooldown.
- Structured logs classify disabled, unsupported, not-found, incomplete, quota-deferred,
  rate-limited, transient, and permanent outcomes. Three consecutive due incomplete
  attempts are marked as a recurring source gap.

## Acceptance cues

- With network + keys configured, quotes appear or update after a scheduled tick.
- With APIs down, UI still shows last cached bars/quotes rather than a blank portfolio.
- A frequent quote tick does not refetch bars, news, or fundamentals; unchanged bars do not
  recompute technicals.
- A frequent quote tick does not refetch long history before its weekly due time.

## Out of scope here

- Full charting suites, Level 2, options chains, complex scanners.
- Email/push notification channels (alerts behaviour is separate; monitoring is the glance surface).
