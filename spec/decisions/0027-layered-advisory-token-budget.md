# 0027 — Layer lenses, digests, and carry-forward for a token-budgeted advisory

Date: 2026-09-25

Revises the lens set in `0009`, the `thesis` lens note in `0013`, the "no separate
fundamentals lens" rule in `0022`, the agent-context exclusion in `0026`, and the
"every subject every day" reading of `0004` and `behaviour/agent-advisory.md`.

## Change

### Lenses follow the ingestion layers

Jev answers every subject×horizon under five lenses, for both books:
`historical`, `fundamentals`, `technicals`, `news`, then `combined`.

- **fundamentals** is the business case: normalized fundamentals, the recorded thesis and its
  falsifiers, and Claude's `thesis_status`. It replaces the `thesis` lens, and its tracker
  counterpart (the entry case and what would make the operator buy).
- **historical** is the long-run record: multi-year returns, drawdowns, volatility, and excess
  return versus the regional proxy. It is context for the case and is never by itself a reason
  to sell (`0013`).
- **combined** receives the position and book context, Claude's per-layer notes, and the four
  lens verdicts. It does not receive the raw layers again. It remains the canonical action.

Older runs keep their `thesis` key in `jev_lenses` and stay readable.

### Deterministic layer cards

Before any model call, the worker condenses each subject into small **layer cards**. Numbers are
rounded, fields are whitelisted, and dates carry no time. The historical card holds only derived
statistics from the `0026` long-history series and its regional proxy. The raw points remain
outside the agent context.

### Materiality-gated carry-forward

The daily run still happens. It re-decides only the subjects whose inputs changed materially
since their last real decision:

- no prior decision, a prior Jev-failure fallback, or a different lens schema
- new linked news
- a fundamentals refresh or a completeness change
- a change in technical trend or RSI zone
- a price move of at least `ADVISORY_MATERIAL_MOVE_PCT` (default 5) since the recommendation
- a thesis edit, or a holding with no thesis
- an edited investor or portfolio profile
- a changed position quantity or lot count
- a changed cash reserve (holdings only)
- the decision being older than `ADVISORY_MAX_CARRY_DAYS` (default 7)
- a forced or single-symbol run

Every other subject **carries forward**. Its last recommendation rows are copied into the new run
with `carried_from_run_id` pointing at the run that actually decided them. Their conversations are
marked carried, and the next comparison stays anchored to that deciding run. Carried rows never
raise alerts.

### Claude runs as a bare researcher

The Claude CLI is invoked with a replaced system prompt, no tools, no MCP servers, no session
persistence, and a JSON Schema for its output. Its usage is recorded on the run. Only changed
subjects are sent, each with its prior per-layer notes and prior combined verdict, so Claude
reports what changed. Claude returns a note of at most 20 words per layer, plus the existing
thesis, entry-case, draft, and info-need fields.

### Jev guidance is sent once

The mandate, book guidance, lens focus, and full criteria definitions travel once in each lens
call's state. Each question carries only its subject, horizon, book, and short criteria labels.
The composite choice keys are unchanged.

## Why

In the measured book of 14 subjects, the per-question text repeated 168 times was about 79% of a
~350k-character Jev run. The default Claude Code system prompt and tool definitions added about
27k input tokens to a one-call research pass. On a quiet day, most subjects have nothing new to
decide. Digesting, scoping each lens to its layer, and carrying unchanged subjects forward keeps
decision quality where evidence changed, and it stops the cost of re-reading unchanged evidence.

## Spec touchpoints

- `spec/behaviour/agent-advisory.md`
- `spec/data.md` (`jev_lenses`, `carried_from_run_id`, `research_json.cards`)
- `spec/tests.md`
