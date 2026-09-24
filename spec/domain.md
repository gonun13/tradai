# Domain

Audience: product / planning. Concepts, rules, and invariants for Tradai.

## Core concepts

| Concept | Meaning |
| --- | --- |
| **Portfolio** | The single local book of European and US holdings. One portfolio in v1 — no multi-portfolio. |
| **Tracker** | The second book: names the operator is considering but does not own (`0019`). Ingested and researched like the portfolio; still the universe a `better_use` sell may point at (`0016`). |
| **Instrument** | A tradeable identity: ISIN (when available), symbol, MIC/exchange, currency, name; **kind** = equity or ETF; **region/venue** covers EU and US listings. ETFs are in scope when listed and bought on the same regular exchange markets as stocks (not a separate venue class). |
| **Holding** | A position in the portfolio: quantity, average cost, optional notes; references an Instrument. |
| **Thesis** | The recorded reason a Holding is owned, plus named falsifiers. Versioned. Claude drafts and records it automatically; the operator may edit it (`0018`). |
| **Tracked name** | An entry on the Tracker. Carries an Instrument, a note, and the date it was tracked. Buying it archives the entry (auto-promote, `0019`). Was "watchlist candidate" before `0019`. |
| **Book** | Which of the two a subject belongs to: `portfolio` or `tracker`. Recorded on every Recommendation, because a name can move between them. |
| **Quote / PriceBar** | Latest quote snapshot and OHLCV history used for monitoring and technicals. |
| **NewsItem** | Optional cached news snippet, optionally tagged to Instrument(s). |
| **Fundamentals** | One normalized, single-provider profile/metrics snapshot for an Instrument, with explicit completeness and provenance (`0022`). |
| **AgentRun** | One scheduled or manual analysis job: status, timing, model/runtime refs, logs. |
| **Recommendation** | Ticker-level advisory from an AgentRun: action, horizon, rationale, confidence, Jev payload. |
| **Alert** | Attention signal derived from a Recommendation when policy says action is warranted. |

## Advisory semantics

- **Actions:** `buy` | `sell` | `hold` | `watch`, plus `drop` — tracker-only (`0019`): stop spending attention on a
  name never owned. It is a suggestion, never a deletion.
- **Reason (required):** every action carries why, because the choice Jev is offered *is* the reason. Sells are
  restricted to `thesis_broken` | `better_use` (`0013`).
- **Two choice sets (`0019`):** a holding is offered buy/sell/hold/watch; a tracked name is offered
  `buy_now` | `wait_better_entry` | `keep_watching` | `drop_lost_interest`. Hold and sell are unrepresentable for
  something you don't own. Both books ride one Jev call per lens — the criteria are per question.
- **Horizons are per book (`0020`):** holdings are `6m` | `12m` | `24m` (`0014`) — `3m` dropped as below the
  operator's holding period, `24m` backing the `no_recovery_24m` loss gate. The tracker is `1m` | `3m` | `6m` —
  an entry-timing question with no loss gate to back, not a multi-year thesis one.
- **Sell doctrine:** price action, momentum and concentration are **never** valid sell reasons. A sell at an unrealised
  loss carries a gate label: `offset_same_year` or `no_recovery_24m` (`0013`). **Not enforced in code** — `0018`
  removed the gate; `doctrine.py` labels the result for the log and changes nothing. Tracker rows carry no gate at
  all, having no position to be at a loss on.
- **Role of Claude Agent CLI:** **researcher** — ingests book/market context, explores scenarios, builds Jev requests, writes research, emits info-needs for ingest planning (subscription auth)
- **Role of Jev:** **decider** — one typed choice per subject x horizon, with confidence; lenses `thesis` | `news` |
  `technicals` | `combined` (`0013` replaced `prices` with `thesis`); canonical displayed action = **combined**.
  Both books get all four lenses; `0022` restored Tracker news.
- **Claude↔Jev dialogue:** bounded back-and-forth; per-instrument transcript shown via UI log icon
- **Human in the loop:** Tradai never places orders; the operator executes elsewhere

See `decisions/0009-claude-researcher-jev-decider.md`.

## Rules and invariants

1. **Two operator profiles** (`0019`) — free text, written once on the Setup page, sent verbatim into every
   Claude/Jev prompt; each falls back to its own default when unset. No structured fields, no approval step.
   - **Investor profile** — who the operator is and what makes a name worth *buying*. Judges the Tracker, and
     travels as global context on every call.
   - **Portfolio profile** — the rules for names already *owned*: sizing, trimming, when to sell, tax. Judges
     holdings.
   See `0012`, revised by `0018`, split by `0019`.
2. **Single user, single portfolio** — no tenancy, no shared accounts.
3. **EU + US listed instruments** — equities and ETFs on supported European venues and US exchanges (e.g. NYSE / Nasdaq). Crypto and non-exchange-listed funds remain out. ETFs use the same quote / bar / advisory paths as stocks; no separate “ETF marketplace” integration.
4. **Advisory-only** — recommendations and alerts inform; they do not execute.
5. **Privacy / agent context:**
   - Portfolio authority remains local SQLite / LAN — no cloud DB, no public hosting of holdings.
   - **Agents receive full portfolio context** needed for good decisions: holdings (quantities, cost basis), weights / concentration, P&L, and EUR totals, plus tickers/ISINs, OHLCV, technicals, and news.
   - This is intentional personal-use tradeoff: richer advice vs ticker-only privacy. Do not strip position data, fundamentals, or Tracker news from Claude / Jev payloads by default.
6. **“Live” monitoring** means polling delayed or near-live quotes — not exchange co-located websockets.
7. **Agent advisory cadence:** recommendations/alerts from agents refresh **once per day after US markets close** (not continuous intraday advisory). Quote monitoring may still poll during sessions.
8. **Alerts are in-app first** — no email/SMS/push required for MVP.
9. **Compliance posture:** personal use; respect each data vendor’s terms (delayed/redistribution limits); not advice to third parties.
10. **Display currency = EUR** — dashboard portfolio value, cost basis totals, P&L, and other money aggregates are shown in **EUR**. Instrument quote/trading currency may still be USD (or other); convert for display using a rate source (implementation detail). Persist native currency amounts where useful; do not invent a multi-currency UI.

## Relations (conceptual)

```
Holding → Instrument ← PriceBar / Quote
                 ↑
AgentRun → Recommendation → Instrument
                ↓
              Alert
```

NewsItem optionally links to Instrument(s) when tagged.

## Locked (see decisions)

- Alert policy: `decisions/0018-simplify-trust-agents.md` (buy/sell and thesis-break → unread alert).
- Daily run clock: `decisions/0011-us-close-schedule-offset.md` (16:30 ET default).

## Unknowns (domain)

- Exact day-one MIC / exchange set for EU and US (depends on actual holdings).
- FX refresh cadence (provider locked to Frankfurter in `0006`; how often to refresh/cache remains implementation detail).
