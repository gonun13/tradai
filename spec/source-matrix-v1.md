# Research-data source matrix — v1

Baseline reviewed 2026-09-24. This is the adoption inventory, not runtime configuration.
Vendor plans and entitlements can change; confirm the linked official documentation before
promoting a candidate.

| Source | Status | Layer / region / kind | Credential | Quota behavior | Known limitation / adoption gap |
| --- | --- | --- | --- | --- | --- |
| Finnhub | Active | Quotes + bars: US equity/ETF; news fallback: US | FINNHUB_API_KEY | Persistent per-minute pacing; honor 429 and Retry-After | Free plans commonly deny candles; Yahoo remains an independent bars fallback |
| Yahoo via yfinance-compatible endpoints | Active | Quotes + bars: EU primary, US fallback; fundamentals: EU primary, US fallback; equity/ETF | None | Conservative pacing and persisted cooldown after throttling | Unofficial Yahoo service; light data can be absent or throttled. The [yfinance API](https://ranaroussi.github.io/yfinance/) documents company info and ETF funds_data, including holdings and allocations |
| FMP | Active when keyed | Fundamentals: US primary, EU fallback when entitled; equity/ETF | FMP_API_KEY | Configurable plan window; persist 429 cooldown | Region and ETF holdings depend on plan. Official [quickstart](https://site.financialmodelingprep.com/developer/docs/quickstart) defines stable endpoints/auth/errors; [pricing](https://site.financialmodelingprep.com/developer/docs/pricing) is the entitlement baseline |
| Marketaux | Active when keyed | News primary: US + EU equity/ETF | MARKETAUX_API_TOKEN | Persist usage/rate headers and cooldown; conservative daily budget | Empty symbol feeds are incomplete and fall through. Official [documentation](https://www.marketaux.com/documentation) defines symbol filtering, UTC dates, 402 usage exhaustion, 429 rate limits, and headers |
| Frankfurter | Auxiliary active | FX to EUR, outside four research layers | None | Independent cached refresh | Business-day reference rates, not live executable FX |
| IBKR stub | Inactive | None | IBKR_ENABLED does not activate routing | N/A | No operational client/session/readiness reporting. Adopt only with unattended gateway health, entitlements, pacing, and quote normalization |
| EODHD | Candidate | Historical EU + US; possible fundamentals add-on | API token | Evaluate plan limits and response headers | Adopt if Yahoo EU completeness or reliability produces recurring gaps |
| Twelve Data | Candidate | Historical US/EU | API key | Credit/rate-plan dependent; 429 handling required | Adopt if active historical routes cannot produce 127 valid daily closes; official [API docs](https://twelvedata.com/docs) are the evaluation baseline |
| OpenFilings / FinancialFilings | Candidate | EU fundamentals | To evaluate | To evaluate | Adopt only if recurring EU fundamentals gaps remain after Yahoo/FMP and licensing, identifiers, freshness, and structured coverage are verified |
| Real IBKR integration | Candidate | Operational quotes, potentially historical where entitled | Gateway session + market-data entitlements | Session and exchange-entitlement specific | Adopt only after readiness can be proved without displacing REST fallbacks; no trading authority is added |

## Adoption rule

A structured source_gap marked recurring=true is evidence to evaluate a candidate, not
permission to enable it. Evaluation must record region/kind coverage, credentials, current
quota/reset behavior, redistribution terms, normalization, completeness results, and the
specific recurring gap it closes.
