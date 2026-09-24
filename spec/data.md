# Data

Audience: engineering. Contracts, models, and persistence for v1. Names-level contracts — not a frozen SQL schema.

## Persistence

- **Store:** SQLite on a named Docker volume.
- **Authority:** Slim PHP owns portfolio writes (holdings). Worker writes agent runs, recommendations, alerts, and cached market data (via API or shared DB — implementation choice under architecture constraints).
- **Scope:** one node, one user, one portfolio. No cloud replication.

## Entities

### Instrument

| Field (logical) | Notes |
| --- | --- |
| isin | Primary market identity when available |
| symbol | Ticker / local symbol |
| mic / exchange | Venue (EU and US regular markets; same path for stocks and ETFs) |
| currency | Quote / trading currency of the listing (e.g. EUR, USD) |
| name | Display name |
| kind | `equity` \| `etf` — both first-class; ETFs are exchange-traded listings, not a separate persistence store |
| region (optional) | `eu` \| `us` — convenience for scheduling / filters; derivable from MIC if preferred |

Referenced by holdings, bars, recommendations, and optionally news tags. Same tables/adapters for both kinds.

### Presentation currency

- **UI money figures are always EUR** (position values, portfolio totals, aggregated P&L, cost shown in portfolio summaries).
- Store native quote currency and amounts as needed; convert to EUR for read models / dashboard.
- FX rates used for conversion are local/cacheable; provider = **Frankfurter** (`0006`).

### Holding

| Field (logical) | Notes |
| --- | --- |
| instrument_id | FK → Instrument |
| quantity | Open position size — rollup from transactions; included in agent context |
| avg_cost | Average all-in cost per unit (native) — rollup; **includes commissions** per `0007` |
| total_cost | Open book cost (native) = sum of lot costs for open quantity |
| notes | Optional holding-level notes |

Belongs to the single local portfolio. Source of truth for acquisitions is **Transaction**, not a lone avg_cost field.

### Tracker entry

| Field (logical) | Notes |
| --- | --- |
| symbol | Unique. The ticker as ingested |
| instrument_id | FK → Instrument. **Mandatory** (`0019`) — ingestion routes on `instruments.region` and a Recommendation FKs to Instrument, so a name-only entry cannot be monitored or decided on |
| name | Display name; falls back to the instrument's |
| note | Free text: why the operator is watching it. The only field maintained after adding |
| added_at / updated_at | Timestamps |
| archived_at | Set when the operator buys the name (auto-promote, `0019`); cleared when the holding is deleted. Archived entries keep their note but leave the Tracker view and the agent context |

Renamed from `watchlist` in `0019`. Mutually exclusive with Holding: a symbol is in exactly one
book at a time, and adding a tracked name that is already owned is a 409.

### Transaction

| Field (logical) | Notes |
| --- | --- |
| instrument_id | FK → Instrument |
| side | `buy` \| `sell` — MVP records acquisitions as `buy`; sells deferred |
| trade_date | Acquisition / trade calendar date |
| quantity | Shares/units |
| unit_price | Price per unit in instrument currency |
| commission | Fees/costs in instrument currency (≥ 0), **included in cost basis** |
| notes | Optional |

Cost rule: `lot_cost = quantity × unit_price + commission`. See `decisions/0007-acquisition-transactions.md`.

### PriceBar / Quote

| Field (logical) | Notes |
| --- | --- |
| instrument_id | FK → Instrument |
| OHLCV bars | Time series for history / technicals |
| latest quote | Snapshot for dashboard |

Cached so the UI can still render when APIs flake.

### NewsItem

| Field (logical) | Notes |
| --- | --- |
| payload / snippet | Cached headline/body as needed for agents/UI |
| instrument links | Optional tags to Instrument(s) |
| source_name | Article publisher |
| adapter_source | Provider that supplied the item; distinct from publisher (`0022`) |

Optional cache — not a hard dependency for every run.

### Fundamentals

| Field (logical) | Notes |
| --- | --- |
| instrument_id | One current snapshot per Instrument |
| payload | Normalized profile and equity ratios or ETF metadata/holdings |
| source / as_of | Single adapter provenance and source timestamp |
| completeness_state | `complete` or `partial` |
| coverage_score / missing_fields | Comparable outcome score and explicit contract gaps |
| updated_at | Local acceptance time |

Provider payloads are never merged field by field.

### Ingestion state

Per Instrument × operation (`quote`, `bars`, `fundamentals`, `technicals`, `news`):
cadence, attempt count, last attempt/success, selected source, coverage score, missing fields,
gap streak, next due time, and optional input fingerprint. Provider rate state separately
persists window counters, last call, cooldown, and observed limit/remaining/reset headers.

### AgentRun

| Field (logical) | Notes |
| --- | --- |
| trigger | schedule \| manual |
| status | e.g. pending / running / succeeded / failed / partial |
| started_at / finished_at | Timing |
| model / runtime refs | Claude / Jev versions as available |
| log ref | Raw worker log pointer or blob reference |
| research | Claude researcher output for the run (notes / synthesis; may be JSON or text) |
| info_needs | Structured list of ingest/feature gaps Claude recommends for better future runs |
| context | Snapshot of the full blob actually fed to Claude/Jev for this run — both books, profiles, cash, realized gains, per-subject quote/fundamentals/technicals/news/thesis. See "Outbound payload contract" below |

### Recommendation

| Field (logical) | Notes |
| --- | --- |
| agent_run_id | FK → AgentRun |
| instrument_id | FK → Instrument |
| book | `portfolio` \| `tracker` (`0019`). **Stored, not derived** — a join would mislabel every historical row the moment a tracked name is promoted. Pre-0019 rows default to `portfolio` |
| action | buy \| sell \| hold \| watch \| drop — **canonical = Jev `combined` lens** (`0009`). `drop` is tracker-only (`0019`) |
| horizon | Portfolio: `6m` \| `12m` \| `24m`; tracker: `1m` \| `3m` \| `6m` (`0020`). Storage retains all five values for current and historical rows. |
| reason | **Required.** Sell: `thesis_broken` \| `better_use`. Buy: `thesis_intact_underweight` \| `new_conviction`. Hold/watch: `thesis_intact` \| `insufficient_evidence` (`0013`). Tracker (`0019`): `entry_now` \| `await_better_entry` \| `insufficient_evidence` \| `lost_interest` |
| loss_gate | `not_at_loss` \| `offset_same_year` \| `no_recovery_24m` \| `blocked` (`0013`). Informational only since `0018`; always null on a tracker row |
| pair_symbol | For `better_use`: the holding or tracked name the proceeds fund. Always null on a tracker row |
| confidence | Decider confidence, promoted from the Jev blob to a first-class column |
| suppressed / suppressed_reason | Vestigial. `0018` removed suppression entirely; nothing writes these any more |
| rationale | Claude research text relevant to this instrument (not a Claude-chosen action) |
| jev_payload | Combined-lens decision + confidence (primary) |
| jev_lenses | Supporting lens answers: `thesis`, `news`, `technicals`, and optionally echoed `combined`. Both books carry all four since `0022`; `prices` remains replaced by `thesis` |
| conversation | Per-instrument Claude↔Jev transcript for this run (turns: researcher requests + decider answers, including scenario rounds) — source for UI log icon |
| timestamps | Created / updated |

Ticker-level recommendations, informed by full book context when agents run. Displayed action always comes from the combined Jev lens; other lenses and the conversation are evidence/transparency.

### Alert

| Field (logical) | Notes |
| --- | --- |
| recommendation_id | FK → Recommendation |
| read / unread | Ack state for dashboard |
| severity | Optional |
| raised_at | When policy fired |

The alert HTTP read model also exposes the joined Recommendation `book` (`portfolio` or
`tracker`) so the shared alert surface can label and route into the correct module (`0021`).
This is derived data, not another Alert persistence column.

## Relations

```
Holding  → Instrument ← PriceBar / Quote
   ↑          ↑   ↑
Transaction ──┘   └── Tracker entry        (0019: two books, one Instrument table)
                  ↑
AgentRun → Recommendation → Instrument
                ↓            (Recommendation.book says which book it was decided about)
              Alert

NewsItem ⋯ Instrument (optional M:N tag — both books)
Fundamentals → Instrument (one current normalized snapshot)
```

## Outbound payload contract (agent context)

Worker → Claude / Jev (and similar) calls **should include** whatever portfolio facts improve advice:

- Tickers / ISINs, kind, venue
- Holdings: quantities, avg cost, weights / concentration, P&L, EUR notionals / totals as needed
- Public OHLCV summaries, fundamentals, technical features, news snippets

Still true:

- No cloud portfolio database; local SQLite remains source of truth
- Do not log or mirror the full book to unrelated third parties beyond the configured agent/LLM/decision providers

The context blob (`AgentRun.context`, built by `worker/advisory.py::_build_context`) is now a
stable, exposed API field. Top level: `display_currency`, `portfolio_market_value_eur`,
`portfolio_cost_eur`, `cash_eur`, `portfolio_total_eur`, `realized_gains_ytd_eur`,
`calendar_year`, `tracker` (compact symbol/name/note list, renamed from `watchlist` in `0019`),
`profiles` (`investor` / `portfolio`, raw — null where unwritten), `mandates` (the resolved text
per book, defaults substituted — what was actually sent), `mandate` (the composed holdings text,
kept for the log page and back-compat), `holdings` (per-instrument quantity/cost/market
value/P&L/weights, `quote`, `fundamentals`, `technicals`, `news`, approved `thesis`), `tracked` (per tracked
name: `book`, `symbol`, `name`, `note`, `added_at`, `quote`, `price_eur`,
`fundamentals`, `technicals`, and `news` — but no position fields),
`built_at`. It is written before Claude's research pass runs and carries no Claude-authored text.

`conversation` turns (`worker/advisory.py::_run_lens` / `_run_scenario_round`) have `role`
(`researcher` \| `decider` \| `system`) and `kind` (`lens_request` \| `lens_answer` \|
`scenario_request` \| `scenario_answer` \| `error`), plus `lens`/`round`/`at`/`summary`/
`hypothesis`/`question_hint` and, for answer turns, `answers: { "<horizon>": { action, payload } }`
where `payload` is Jev's raw per-question answer passed through verbatim (at minimum `choice`
and `confidence`; further keys are Jev/TypeSafe-defined and not enumerated here).

## Unknowns

- Concrete SQL types, migrations tool, and whether worker shares the SQLite file vs writes only through Slim.
- Canonical unique key for Instrument (ISIN-only vs ISIN+MIC).
- Exact JSON shape of `jev_payload` / `jev_lenses`, and of each `answers[horizon].payload` beyond `choice`/`confidence` (depends on Jev / TypeSafe System One integration — external, not controlled by this codebase).
- Exact JSON shape of `info_needs` (implementation detail; `conversation` and the context blob are now locked down above).
