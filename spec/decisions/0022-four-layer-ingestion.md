# 0022 — Four-layer outcome-oriented ingestion

Date: 2026-09-24

Supersedes the adapter/fallback mechanics in 0006 and the Tracker-news/three-lens
parts of 0019. Frankfurter FX remains an auxiliary service outside these layers.

## Change

Ingestion is four independently due datasets:

1. **Historical** — quotes and daily bars are separate operations. US routes Finnhub →
   Yahoo; EU routes Yahoo. A future operational adapter can be inserted without changing
   the selection contract.
2. **Fundamentals** — US routes FMP → yfinance; EU routes yfinance → FMP where the
   operator's FMP plan covers the listing.
3. **Technicals** — calculated locally from the accepted bar snapshot, and recalculated
   only when that snapshot changes. The registry has an extension point for future sourced
   indicators.
4. **News** — US routes Marketaux → Finnhub; EU routes Marketaux. Both books are covered.

Each operation has an ordered adapter registry. The worker stops on the first response that
meets the operation's completeness contract. It never merges providers. If every response is
partial, the highest coverage/freshness score is the only candidate; an existing cache wins
when it is more complete, or equally complete and at least as fresh.

## Completeness

- Quote: positive price, currency, and timestamp.
- Bars: at least 127 distinct valid daily closes. OHLCV presence improves partial score.
- Fundamentals: identity plus, for equities, one value in each of valuation,
  profitability, growth, and leverage/liquidity; for ETFs, identity plus AUM or expense
  data and holdings or allocation data.
- Technicals: RSI14, SMA20/50, 1m/3m/6m returns, and distance from both SMAs.
- News: at least one deduplicated, instrument-linked item in the recent-news window.
  A successful empty response falls through.

## Persistence and operation

- fundamentals holds one normalized source snapshot, its provenance, as-of time,
  completeness, score, and missing fields.
- ingestion_state holds cadence, attempts, success/source/coverage state, gap streak,
  next due time, and the bar input fingerprint per instrument and operation.
- provider_rate_state persists pacing windows, counters, cooldowns, and observed vendor
  limit headers across worker restarts. Retry-After and vendor headers override
  conservative environment-configurable defaults.
- News records adapter_source separately from article publisher source_name.
- Cadences are quote 15 minutes, bars/news daily, fundamentals seven days, and technicals
  on accepted-bar change. FX keeps its own cache cadence.
- Work is ordered oldest-success-first across active instruments in both books. A provider
  in cooldown is skipped without blocking other providers or instruments.
- Manual ingest always refreshes quotes and performs other datasets only when due.
  force_news=1 bypasses news freshness, but not a hard quota/cooldown.
- Every fallback, quota deferral, partial selection, retained cache, and gap is emitted as
  structured worker JSON. A third consecutive due incomplete attempt is marked recurring.
  Source adoption stays a human decision.
- Each ingest report includes per-layer `ingested`, `from_cache`, and `missing` counts in
  instrument-dataset units. Historical also exposes its quote/bars operation breakdown.

## Advisory and interface

Fundamentals enter both book read models and stable advisory context. Claude research and
the thesis/combined Jev lenses receive them; there is no new dashboard panel or lens.
Tracker news is restored, so portfolio and Tracker both use thesis, news, technicals,
and combined.

Existing refresh/report keys remain compatible. Coverage gaps remain operational logs, not
a new public coverage API. IBKR_ENABLED=1 cannot activate the existing stub; IBKR remains
outside routing until it reports operational readiness.

## Why

Provider availability is not dataset quality. Per-operation completeness makes fallbacks
deterministic, prevents mixed-source snapshots, protects good cache from degradation, and
lets one quota or outage degrade only its layer.

## Source governance

The versioned evaluation inventory is spec/source-matrix-v1.md. Adding an active source
requires a reviewed update to that matrix and, when behavior changes, another decision.
