# 0006 — Market-data adapters (capability × region)

Date: 2026-09-20

Adapter ordering, completeness, fallback, and quota behavior are superseded by 0022.
Frankfurter FX and the source-selection rationale remain in force.

## Change

Lock how external market, news, fundamentals, and FX data are sourced: separate adapters by capability and region, mix-and-match defaults, prefer vendors that cover the most slots.

## Decision

### Adapter shape

- Implement **separate adapters** (not one god-client) for:
  - **Historical** — quotes + OHLCV bars
  - **News** — headlines / snippets for instruments
  - **Fundamentals** — light profile / statements / ETF meta (thin OK for MVP; rich depth remains deferred)
  - **FX** — rates for EUR display conversion
- Each adapter declares supported **regions** (`US`, `EU`) and is bound independently in config so providers can be mixed.
- Prefer a vendor that fills **more capability × region cells** when quality/quota are comparable; split only when a free/cheap tier leaves a cell empty or too weak.
- One vendor package may back multiple adapter classes (shared HTTP client/key) but bindings stay separate.

### Default free mix (MVP)

| Slot | Primary | Fallback |
| --- | --- | --- |
| Historical quotes + bars — **US** | Finnhub | Twelve Data or FMP |
| Historical quotes + bars — **EU** | yfinance | EODHD (if paying for All-World) |
| News — **US + EU** | Marketaux (one adapter, both regions) | Finnhub company news (US-only backup) |
| Fundamentals — **US** | FMP or Finnhub profile | — |
| Fundamentals — **EU** | yfinance light; EODHD Fundamentals if paying | OpenFilings / FinancialFilings later |
| FX → EUR | Frankfurter (ECB / official CB rates, no key) | — |

### Optional: Interactive Brokers (IBKR Ireland)

- Add **optional** `IbkrHistoricalAdapter` when an IBKR.ie (or equivalent IBKR) account + Client Portal Gateway / TWS Gateway session is available.
- **Use for:** dashboard quotes — free US streaming (Cboe One / IEX, non-consolidated) and free **delayed** EU Level 1 (e.g. Euronext, Xetra) where offered.
- **Do not use as sole provider:** API historical bars generally require Level 1 exchange subscriptions (delayed streaming ≠ free OHLCV); news and Reuters fundamentals need separate paid products; retail gateway auth (browser + 2FA, session tickle) is heavier than REST keys.
- When IBKR session is up, it may be preferred for **quotes** US/EU; nightly **bars**, **news**, and default **fundamentals** stay on the free mix above unless the operator explicitly enables paid IBKR packs.
- IBKR is **data-only** for Tradai — no order routing, no required portfolio sync (advisory-only / local book remains authority).

### Cheap paid upgrade path (optional)

- If one paid vendor is preferred for breadth: **EODHD** for Historical EU+US (and Fundamentals add-on); keep **Marketaux** for news; keep Finnhub or IBKR for higher-frequency US quotes if needed.

### Out of default mix

- Alpha Vantage / Marketstack as primaries (quotas too low for a book of dozens).
- Massive/Polygon free as EU source (US-focused).
- Stooq as primary (no proper REST API).

## Why

- Free tiers are usually US-strong / EU-weak; dual Historical adapters are required for an EU+US book without paid data.
- Separate capability adapters let news (Marketaux) and US bars (Finnhub) win independently of EU history (yfinance / EODHD).
- IBKR is strong for broker-grade quotes if the operator already banks there, but must not block unattended Docker runs or replace REST bars/news.

## Spec touchpoints

- `spec/PROJECT.md`, `spec/architecture.md`, `spec/domain.md`, `spec/data.md`
- `spec/behaviour/monitoring.md`
- Clarifies open “market-data provider(s)” and FX source from earlier specs
- Complements `0002-us-listings`, `0003-display-eur`
