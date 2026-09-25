# 0028 — Claude explains each recommendation

Date: 2026-09-25

Builds on `0009` (Claude researches, Jev decides) and `0027` (layer cards, carry-forward).

## Change

After Jev's combined lens (and any scenario rounds) has decided, Claude makes one more pass: it
**explains** each decision to the operator in plain language. It sees, per decided subject:

- the combined choice for every horizon: action, reason, confidence, and Jev's top choice
  probabilities
- the four evidence-lens verdicts
- its own layer notes, research, and thesis or entry case
- the position card

It also receives a run-wide backdrop:

- **market** cards for the regional proxies already cached by `0026` (SPY for the US, EXSA.DE for
  Europe): 1m/3m/1y returns, distance from the 52-week high, volatility
- a book-wide **headline tape**: at most 12 deduplicated ingested headlines, newest first
- the research synthesis

No new data source is added. The market read comes only from these inputs.

Claude returns, as schema-validated JSON:

- `market_read`: 2–3 sentences on the overall market mood.
- `explanation` per subject: at most about 90 words on why the combined lens landed where it did,
  which evidence carried it, how sure Jev was, and how the market backdrop bears on it.
- `tension` per subject: one sentence when a lens verdict, the research, or Jev's own
  probabilities point meaningfully the other way; otherwise null.

The explanation **never changes the action**. It runs after the decision and nothing reads it
back into one. It is stored on each of the subject's recommendation rows as
`explanation = {text, tension, market_read}`, added to the transcript as an `explanation` turn,
and recorded on the run (`research.market_read`, `research.explanations`,
`token_budget.claude_explain`). Carried rows keep the deciding run's explanation.

The operator sees it in three places:

- a **Why** section at the top of each ticker's log page
- a paragraph, plus any tension, under each ticker in the Portfolio and Tracker panels
- a **Market read** box above each panel's list

It is deliberately separate from `rationale` and `research`, which stay unrendered.

The explain step is best-effort. A Claude failure or an unusable answer is logged
(`claude explain skipped: …`), and the run persists its decisions without explanations.

## Why

The run showed what was decided and each lens's verdict, but not why the combined decision
followed from the evidence and the market. A short explanation written after the fact, with a
visible tension note, makes each decision easy to audit without letting the researcher override
the decider. The operator accepted the extra Claude call's tokens for this.

## Spec touchpoints

- `spec/behaviour/agent-advisory.md`
- `spec/data.md`
- `spec/tests.md`
