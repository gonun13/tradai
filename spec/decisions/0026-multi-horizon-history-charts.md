# 0026 — Multi-horizon historical performance charts

Date: 2026-09-25

Extends the Historical layer in `0022` without changing the daily bars used by technicals,
daily change, outcomes, or advisory scoring.

## Decision

Portfolio and Tracker rows expose an on-demand adjusted-performance chart for **1Y, 2Y, 5Y,
and Max** (default 5Y). Long history is a separate weekly operation and cache:

- Yahoo adjusted closes are fetched as complete history, then rebuilt into a compact series.
- The latest year keeps every trading-day point; years 1–5 keep the last valid point of each ISO
  week; older history keeps the last valid point of each calendar month.
- Provider failure, truncation, or an inferior coverage interval retains the accepted cache.
- `price_bars` and daily bars ingestion remain unchanged.

Regional comparison proxies are cached once per active region: adjusted **SPY** for the S&P 500
in the US and adjusted **EXSA.DE** for the STOXX Europe 600 in Europe. Benchmark failure is
reported independently and never blocks instrument history, normal ingestion, or rendering.

The API normalizes stock and benchmark to 100 at their first common observation. Max retains any
earlier stock points relative to that common baseline and identifies when comparison begins. If
the benchmark is absent, the stock is normalized alone and a warning is returned. Performance is
calculated in each series' native currency; historical FX conversion is deliberately absent and
different currencies are labelled.

Long-history points are excluded from Claude/Jev context and from Agent context preview.
`0027` revises this for the agents only: the points still never leave the worker, but derived
statistics computed from them (multi-year returns, drawdowns, volatility, excess return versus the
regional proxy) form the historical layer card.

## Why

Medium-horizon decisions benefit from seeing total adjusted price performance against a familiar
regional reference. A compact, read-only chart supplies that context without turning Tradai into
the full charting suite excluded by `PROJECT.md`.

## Sources

- [Yahoo SPY history](https://finance.yahoo.com/quote/SPY/history/?p=SPY)
- [Yahoo EXSA.DE](https://de.finance.yahoo.com/quote/EXSA.DE/)
- [STOXX Europe 600 benchmark description](https://stoxx.com/index/sxxp/)

Yahoo's adjusted-close field includes split and distribution adjustments; the proxy currencies
remain USD for SPY and EUR for EXSA.DE.
