# 0019 — The tracker: a second book, researched like the portfolio

Date: 2026-09-20

**Revises `0016`** (the watchlist becomes a monitored book, not a bare candidate list) and
**partially reverses `0018`** (the watchlist UI comes back, and the single investor profile
splits in two).

## Change

### The tracker

- `watchlist` is renamed **`tracker`** — table, API routes, agent context key, spec prose.
  One name across the stack. `instrument_id` becomes **NOT NULL**: the ingest loop routes on
  `instruments.region` and a recommendation FKs to `instruments`, so a tracked name without
  one cannot be monitored or decided on. Existing rows have their instrument minted on
  migration.
- A new `archived_at` column carries **auto-promote**: recording an acquisition for a tracked
  symbol archives its row (keeping the note and the date it was tracked); deleting the
  holding un-archives it. A symbol therefore appears in exactly one tab at a time.
- **Ingestion covers both books.** `refresh.py` changes `INNER JOIN holdings` to a union of
  holdings and unarchived tracker instruments, tagging each with a `book`. Quotes, bars and
  technicals run identically for both.
- **News does not.** `_refresh_news` stays holdings-only. Marketaux's free tier is 100
  requests/day and the operator's attention budget belongs to what they own.
- **Both books ride one daily run.** A single context carries `holdings` and `tracked`, so
  "sell A to fund B" is finally reachable in one pass — the gap `0016` was written to close.

### Two profiles

`settings.investor_profile_text` splits into two free-text fields:

| Field | Means | Applies to |
|---|---|---|
| `investor_profile_text` | Who the operator is, and what makes a name worth **buying** | The tracker — and travels as global context on every call |
| `portfolio_profile_text` | The rules for names already **owned**: sizing, trimming, when to sell, tax | Holdings |

`doctrine.compose_mandate(profiles, book)` builds the text a book is judged by: the investor
profile alone for the tracker, investor + portfolio for holdings. Each half falls back to its
own default (`DEFAULT_INVESTOR_PROFILE` / `DEFAULT_PORTFOLIO_PROFILE`) so the system still
works before either is written. The resolved text per book is recorded on the run context as
`mandates`, so the log page shows what was really sent rather than going blank.

**Migration:** the operator's existing text reads as portfolio-management rules (tax,
realising losses), so it **moves to `portfolio_profile_text`** and `investor_profile_text`
starts empty on the default. Holdings keep exactly the mandate they had; the Setup page warns
that tracked names are judged generically until the investor half is written.

### Tracker verbs

A name you don't own cannot be held or sold, so Jev gets a second choice set. System One takes
per-question criteria, so **both books still ride one call per lens** — no extra round trips.

| Choice | → action | reason |
|---|---|---|
| `buy_now` | `buy` | `entry_now` |
| `wait_better_entry` | `watch` | `await_better_entry` |
| `keep_watching` | `watch` | `insufficient_evidence` |
| `drop_lost_interest` | **`drop`** | `lost_interest` |

`recommendations` is rebuilt (SQLite cannot ALTER a CHECK) to admit `drop`, the three new
reasons, and a stored **`book`** column. `book` is stored rather than derived: a join would
mislabel every historical row the moment a tracked name is promoted.

`drop` never deletes anything — it is a suggestion the operator acts on, or doesn't.

### Three lenses, not four

Tracked names are asked `thesis`, `technicals` and `combined`, and **left out of `news`**
entirely rather than asked a question with no evidence behind it. On the `thesis` lens the
question mirrors: not "does the reason for owning this still hold" but "does the case for
buying it". Claude returns `entry_case` and `what_would_make_me_buy` in place of
`thesis_status` / `evidence`.

### Search

`GET /instruments/search` — local `instruments` first (flagged `known`), then Yahoo
(`query1.finance.yahoo.com/v1/finance/search`, the host the quote adapter already uses, no
key) and Finnhub `/search`, merged and deduped on symbol with Yahoo winning collisions.
Each source is tried independently, so one being down degrades the result instead of failing
it, and manual ticker entry stays open regardless.

Neither source returns a currency. It is resolved on **add** — from the existing instrument
when the symbol is already known, otherwise inferred from the region and confirmed by the
first quote.

### UI

- A `default` layout owns the header and the **Portfolio | Tracker** tabs. The pages stop
  hand-rolling their own heroes, and `/` stops duplicating the ops buttons that already live
  in `GlobalOpsMenu`. Ingest and Run stay global — one run covers both books.
- `RecommendationsPanel` takes a `book` prop; **each tab shows only its own book's rows**. A
  sell carrying `pair_symbol` links across to the named alternative.
- The Tracker table shows price (native + EUR), 1m, 3m, RSI14, the note and the added date —
  what the agents actually reason about, since there is no cost basis to show.

## Why

Operator: *"lets build a tracker… tracker will behave same as portfolio regarding ingesting
data and agents research… this will allow to have different rules for agents to analyse a
stock."*

`0016` added a watchlist so a `better_use` sell had a name to point at, then `0018` removed
its UI as bureaucracy — correctly, because it was a list of tickers that did nothing. The
gap that remained is the one `0016` actually named: **the system could only ever see what was
already owned.** Making the tracker a real book, on the same ingest and the same run, closes
it for real rather than by declaration.

The profile split is the same argument at the prompt level. One mandate had to serve two
different questions — "should I keep this?" and "should I start this?" — and the operator's
own text answers only the first. Two fields is one more textarea, not a workflow; it does not
reintroduce anything `0018` objected to (no approval gates, no curation step, no code-level
enforcement). **Whatever Jev decides is still what gets written.**

## Spec touchpoints

- Revises `spec/decisions/0016` (watchlist → tracker; a monitored book, not a name list)
- Revises `spec/decisions/0018` (tracker UI restored; one profile becomes two)
- `spec/domain.md`, `spec/data.md`, `spec/stages.md`
- `spec/behaviour/tracker.md` (new), `spec/behaviour/portfolio.md`,
  `spec/behaviour/monitoring.md`, `spec/behaviour/agent-advisory.md`
