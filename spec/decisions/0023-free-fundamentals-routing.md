# 0023 — Free fundamentals provider routing

**Status:** accepted

## Decision

Replace FMP fundamentals routing with free-provider routes selected by region and instrument
kind:

| Instrument | Ordered route |
| --- | --- |
| US equity | Finnhub → Alpha Vantage |
| EU equity | Alpha Vantage → Yahoo |
| US or EU ETF | Alpha Vantage → Yahoo |

Every accepted snapshot still comes from one provider. Responses are normalized into the
existing Fundamentals contract, evaluated with the existing completeness score, and may not
replace a more complete or equally complete fresher cache.

Alpha Vantage equity snapshots combine `OVERVIEW` and the latest `BALANCE_SHEET` and reserve
two of the persisted 25-call daily budget before the attempt. ETF snapshots use `ETF_PROFILE`
and reserve one call. Known European symbols are translated without a search request:
`.DE`/XETR/XETA → `.DEX`, `.F`/XFRA → `.FRA`, `.PA`/XPAR → `.PAR`, and
`.DU`/XDUS → `.DUS`. Unknown mappings and US symbols are attempted unchanged.

Complete fundamentals remain on a seven-day cadence. A missing or partial accepted cache is
retried after 24 hours and whenever the operator selects **Ingest now**. Manual retries do not
bypass provider cooldowns or quotas. Existing FMP cache rows remain valid under the normal
cache-protection rule, although FMP is no longer an active adapter.

Configuration and Setup expose `ALPHA_VANTAGE_API_KEY` and no longer expose `FMP_API_KEY`.
Instrument `kind` stays operator-owned and is never inferred or rewritten by a provider.

## Why

The routes provide useful equity and ETF fundamentals without a paid FMP entitlement while
keeping quota use predictable. Region-and-kind routing avoids spending Alpha Vantage's small
daily allowance on a less suitable endpoint and retains Yahoo as the keyless EU/ETF fallback.

## Revisions

- Revises the Fundamentals route and cadence details in `0022-four-layer-ingestion.md`.
- Revises the Alpha Vantage exclusion and Fundamentals row in
  `0006-market-data-adapters.md`; its low quota is accepted here with persistent accounting.

## Rollout

Correct local instrument classifications through the existing holding update path:
`DDD` is an equity and `JEDI.DE` is an ETF. No symbol-specific migration belongs in code.
