# Behaviour — Tracker

Sibling of `portfolio.md`. The tracker (`0019`) is the operator's second book: names they are
considering but do not own. It is monitored and researched on the same cadence as the
portfolio — the difference is the question being asked, not the level of attention.

## Purpose

Answer "should I start a position in this?" with the same machinery that answers "should I
keep this?", and give a `better_use` sell (`0013`) a real named alternative to point at
(`0016`).

## Primary flow

1. Operator searches by company name or ticker (`GET /instruments/search`): local instruments
   first, then Yahoo and Finnhub merged behind them. Manual ticker entry is always available.
2. Selecting a hit prefills an add form the operator can correct — symbol, name, kind, region,
   currency, MIC, ISIN, plus a free-text note on why they are watching it.
3. Adding creates (or reuses) the `instruments` row and the `tracker` row. A symbol that is
   already a holding is rejected with a 409 — the two books are mutually exclusive.
4. The next ingest pulls quotes, bars and technicals for it alongside the holdings.
5. The next daily run researches it: Claude writes an entry case, Jev decides per horizon.
6. Recommendations appear on the Tracker tab; a `buy` raises an alert like any other.

## Secondary flows

- **Edit the note** — the only field the operator maintains after adding.
- **Remove** — deletes the entry and its note outright.
- **Bought it** — links to the Portfolio tab's acquisition form with the symbol prefilled.
  Recording the acquisition is what archives the tracker entry.

## Rules

- **One instrument per tracked name, and it is mandatory.** Ingestion routes on
  `instruments.region`; a recommendation FKs to `instruments`. A name-only entry cannot be
  monitored or decided on.
- **Auto-promote.** Buying a tracked symbol archives its row (note and added date preserved);
  deleting the holding un-archives it. Archived entries leave both the Tracker view and the
  agent context.
- **Prices yes, news no.** Quotes, bars and technicals are fetched for tracked names on every
  ingest. News is not — the Marketaux free tier is 100 requests/day and is spent on the book
  the operator owns.
- **Three lenses.** Tracked names are asked `thesis`, `technicals` and `combined`. They are
  omitted from the `news` lens rather than asked a question with no evidence behind them.
  On the `thesis` lens the question mirrors the portfolio's: whether the case for *buying*
  holds, not whether the reason for *owning* still does.
- **Its own horizons: `1m` / `3m` / `6m`, not the holdings' `6m` / `12m` / `24m` (`0020`).**
  An entry-timing question stays short; `24m` exists to back the `no_recovery_24m` loss gate,
  which the tracker never has. The table's gain columns (`1m`, `3m`, `6m`) line up with the
  same set.
- **Its own verbs.** `buy_now` / `wait_better_entry` / `keep_watching` / `drop_lost_interest`.
  Sell and hold are unrepresentable here. `drop` is a suggestion, never a deletion.
- **Judged by the investor profile**, not the portfolio profile (`0019`). Position sizing and
  sell doctrine are not questions you can ask about something you don't own.
- **No loss gate, no `pair_symbol`** on a tracker row — there is no position to be at a loss
  on and no proceeds to redeploy.
- **Advisory only.** As everywhere else, nothing here places an order.

## Out of scope

- **Open-universe screening** — the agent proposing names the operator has never considered.
  Still deferred (`0016`); it needs a screener and a fundamentals feed that do not exist.
  The tracker universe is operator-curated by design.
- Price alerts or target-price triggers. Entry timing is Jev's judgement from technicals and
  the investor profile, not a threshold the system watches.
- Per-name ad-hoc research runs. One combined daily run covers both books.
