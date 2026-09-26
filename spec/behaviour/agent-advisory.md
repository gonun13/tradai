# Behaviour — Agent advisory

Daily post–US-close analysis producing horizon-aware recommendations and in-app alerts. Advisory only. Agents get full portfolio context.

Roles are locked in `decisions/0009-claude-researcher-jev-decider.md`: **Claude researches**; **Jev decides**.

## Goals

- Run once per day after US markets close without the operator babysitting.
- Combine **full book context** (positions, costs, weights, held-days, cash, realised YTD gains, totals in the run's snapshotted display currency) with
  the recorded thesis, news and local technicals into buy / sell / hold / watch advice for **6m / 12m / 24m**
  horizons on holdings (`0014`), and buy_now / wait_better_entry / keep_watching / drop_lost_interest advice for
  **1m / 3m / 6m** horizons on the tracker (`0020`) — equities and ETFs. Every action carries a **reason** (Jev's
  choice set encodes it); a sell also gets an informational **loss_gate** label. Neither is enforced by code
  (`0018`) — Claude and Jev are given the investor profile and the sell-doctrine guidance directly and decide.
- Test each holding's **thesis** (`0015`, auto-written, no approval step) against new evidence and report
  `intact | weakening | broken` with the evidence that moved it. Draft one for any holding that lacks it —
  used the same run.
- Use Claude Agent CLI (subscription auth) as **researcher** (ingest context, scenario exploration, build Jev requests, emit info-needs) and Jev as **decider** (typed actions + confidence across lenses).
- Raise in-dashboard alerts when a recommendation warrants attention.
- Expose per-ticker Claude↔Jev conversation logs from the UI (log icon).

## Flows

### Daily post–US-close run (primary)

1. Scheduler fires **once per day after US markets close** (NYSE/Nasdaq regular session; exact minutes after the bell / timezone / holiday skip open).
2. Worker loads **both books** — holdings and tracked names — and condenses each subject's ingested layers
   into deterministic **layer cards** (`0027`): historical (derived long-run statistics, never raw points),
   fundamentals (whitelisted metrics, coverage, and for holdings the thesis), technicals, news, and position.
   One run covers both books.
3. **Materiality gate** (`0027`): each subject's cards are compared with those its last real decision was made
   on. Only subjects with a material change (new news, fundamentals refresh, technical zone change, price move
   ≥ `ADVISORY_MATERIAL_MOVE_PCT`, thesis/profile/position/cash change, decision older than
   `ADVISORY_MAX_CARRY_DAYS`, forced or single-symbol run, or no comparable prior) are re-decided. The rest
   **carry forward**: their last rows are copied with `carried_from_run_id`, and they raise no alerts. If
   nothing changed, the run makes no model calls at all.
4. **Claude (researcher)** receives the changed subjects' cards with their prior decision and notes, and
   returns per-layer notes (≤20 words each), thesis findings, drafts and info-needs as schema-validated JSON.
   It runs with a replaced system prompt and no tools.
5. **Jev (decider)** answers Choice questions for each changed subject×horizon under five lenses:
   `historical`, `fundamentals`, `technicals`, `news`, then `combined`. Each evidence lens sees only its own
   layer card and Claude's note on it; `combined` sees the position, every note, and the four evidence
   verdicts. `fundamentals` absorbed the `thesis` lens — the `prices` lens stays gone, because judging on
   quote and P&L alone is precisely the reasoning `0013` rejects; `historical` is context, never by itself a
   reason to sell. Tracked names get the same five lenses (`0022`, `0027`), their own choice set
   (`buy_now` / `wait_better_entry` / `keep_watching` / `drop_lost_interest`), and the investor profile alone.
   Shared guidance (mandate, lens focus, book guidance, full choice definitions) travels once per call; each
   question names only its subject, horizon and book, so both books still ride **one call per lens**.
6. Claude may run further scenario rounds with Jev, bounded by `ADVISORY_MAX_SCENARIO_ROUNDS` (default **0**, maximum **10**); each turn is recorded in the per-instrument conversation transcript.
7. Claude emits **info-needs** — structured recommendations for ingest/features that would improve the next run.
8. `worker/domain/doctrine.py` parses Jev's composite choice into (action, reason) and computes an informational
   `loss_gate` label for the log. **It gates nothing** — `0018` removed suppression, and whatever Jev decides is
   what gets written. Tracker rows carry no gate and no `pair_symbol`, having no position to be at a loss on.
9. **Claude (explainer)** (`0028`) explains each decided subject's combined decision in plain language — at
   most ~90 words, plus a one-sentence *tension* when a lens, the research or Jev's own probabilities point the
   other way — and writes a run-wide **market read** from the regional proxies and the book-wide headline tape.
   It runs after Jev decides and never changes an action; a failure is logged and the run keeps its decisions.
10. Worker persists `AgentRun` (research, info-needs, model refs incl. a token budget, logs, and each subject's
   decision record) + per-subject `Recommendation` rows, each tagged with its `book` (`0019`). **Canonical `action` = combined lens.** Supporting lens payloads and the
   Claude↔Jev transcript are stored for the UI.
11. Alert policy (`0017`, simplified by `0018`) creates `Alert` rows for any buy/sell and for thesis breaks. This
   covers a tracked name Jev says to buy now; `drop` does not alert. Carried rows never alert (`0027`).
12. Each module shows its own book's recommendations, each ticker's explanation, the market read, and the
    shared unread alerts until the next daily run (unless manually re-run). Operator can open a **log icon per
    ticker** to read the **Why** section and that instrument's Claude↔Jev conversation for the run.

### Optional “run now”

1. Operator may trigger via UI → Slim API (debug / catch-up) — does not replace the daily post–US-close schedule.
2. Same pipeline and same two-book context as the scheduled run.
3. Job status visible through Slim read models (preferred BFF).

### Acknowledge alert

1. Operator opens alert in UI.
2. Marks read / acked via Slim.
3. Alert no longer counts as unread attention.

### View ticker conversation log

1. Operator clicks the log icon on a ticker / recommendation row.
2. UI loads that instrument’s Claude↔Jev transcript for the selected (default: latest) `AgentRun`.
3. Transcript shows researcher turns (Claude) and decider answers (Jev), including lens passes and any scenario rounds.

### View advisory operation history

1. **Advisory logs** lists the newest 20 scheduled, manual, forced, and targeted runs.
2. Selecting a run loads only its operational detail: status, effective trigger, timestamps,
   error, and persisted log lines such as `context …` and `materiality …`.
3. Research and full context are excluded from the history list and log-detail contract. Run
   metadata, errors, and worker log text do not render in the recommendation panel.
4. Manual global and targeted runs use the shared utility flash for success, partial, cached,
   timeout, and failed outcomes and refresh both books, recommendations, alerts, elapsed status,
   context previews, and open history.

## Rules

- **No auto-execution** of trades.
- **No `ANTHROPIC_API_KEY`** in the worker for the intended path — subscription OAuth / `CLAUDE_CODE_OAUTH_TOKEN` only.
- **Full portfolio access for agents** — quantities, cost basis, weights, P&L, and display-currency totals as needed for good decisions (see `domain.md` / `data.md`). New contexts use `_display` keys; historical EUR snapshots remain readable.
- **Two books, two mandates** (`0019`) — holdings are judged by the investor + portfolio profiles together;
  tracked names by the investor profile alone. Position sizing and sell doctrine are not questions you can ask
  about something the operator doesn't own.
- **Layer placement (`0027`):** Claude receives every layer card of the subjects it researches. Each evidence
  lens receives only its own layer's card; `combined` receives the verdicts and notes, not the raw layers.
  Long-history points never leave the worker — only derived statistics (`0026`).
- **Carry-forward is not a skipped run.** The daily run still happens and still produces a full set of current
  recommendations; unchanged subjects simply keep the decision already made, anchored to the deciding run.
- **Claude explains, never decides** (`0028`). The explanation is written after the decision and stored
  beside it; nothing reads it back into an action.
- **Jev alone decides** the displayed action; Claude does not override combined-lens choices, and nothing in
  the worker overrides Jev's choice either (`0018`).
- **Guidance, not enforcement.** The sell-doctrine reasoning (`0013`) — thesis broken or better use, not price
  alone — is given to Jev and Claude in their prompts. They apply it; the worker only labels the result.
- **Cadence:** agent recommendation updates are **daily after US markets close**, not continuous intraday advisory. Quote monitoring may still poll during sessions.
- Alerts are in-app for MVP; external notification hooks are later.
- Failures must leave a failed/partial `AgentRun` state visible — no silent swallow.
- Claude↔Jev rounds are **bounded** (default 0, configurable up to 10) after the initial lens pass.

## Acceptance cues

- After a successful run: recommendations exist with action (= combined lens), horizon, Claude research/rationale, supporting lens payloads, and confidence/Jev payload informed by the actual book.
- Info-needs are stored on the run and visible for ingest planning.
- Log icon per ticker shows the Claude↔Jev conversation for that instrument.
- Each freshly decided ticker shows Claude's explanation (and tension when given) in its panel and on its log
  page; carried tickers show the deciding run's explanation; each panel shows the market read.
- Crossing the attention policy creates an unread alert.
- Operator can ack alerts.
- Agent/LLM payloads for a run include holding sizes / costs when positions exist (not ticker-only stubs).

Regression checks: `spec/tests.md`.

## Unknowns

- Whether Slim is sole status BFF or worker exposes LAN endpoints too (MVP: Slim BFF; worker stays internal).
- Exact JSON shapes for info-needs and conversation turns (implementation detail).

Alert policy: the simplified policy in `0018-simplify-trust-agents.md` (building on `0010`/`0017`). Schedule clock: `decisions/0011-us-close-schedule-offset.md`. Auth: `decisions/0008-claude-docker-oauth-token.md`. Roles: `decisions/0009-claude-researcher-jev-decider.md`.
