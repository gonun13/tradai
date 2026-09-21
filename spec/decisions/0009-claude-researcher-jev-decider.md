# 0009 — Claude researcher / Jev decider

Date: 2026-09-20

## Change

Lock advisory roles: Claude researches and builds Jev requests; Jev alone decides buy/sell/hold/watch. Multi-lens Jev passes, bounded Claude↔Jev rounds, info-needs for ingest planning, and per-ticker conversation logs for the UI.

**Current implementation note:** the initial decision's default of 3 scenario rounds has since been reduced to
`ADVISORY_MAX_SCENARIO_ROUNDS=0` by default; the worker accepts values from 0 through 10.
The portfolio `prices` lens was later replaced by `thesis`, and tracker subjects use `thesis`,
`technicals`, and `combined` with tracker-specific choices (`0013`, `0019`).
The original decision bullets below are retained as history; the current behavior is defined by
the implementation note and the later decisions.

## Decision

- **Claude** = researcher. Ingests collected book + market features, explores scenarios, builds TypeSafe System One (Jev) requests, may call Jev multiple times, writes research text, and emits **info-needs** (structured gaps for future ingest).
- **Jev** = sole **decider** of `buy` | `sell` | `hold` | `watch` per symbol×horizon.
- **Lenses** (Jev runs for each): `prices` | `news` | `technicals` | `combined`.
- **Canonical UI action** = Jev **`combined`** lens only. Other lenses are supporting evidence, not the primary chip.
- After the initial four-lens pass, Claude may drive up to **3** further scenario rounds with Jev (env-tunable later; default 3). Worker orchestration enforces the cap.
- Persist: Claude research, all lens answers, info-needs on the run, and a **per-instrument Claude↔Jev conversation transcript** for the UI log icon.
- Stage 5 one-shot pipeline remains the shipped MVP until **Stage 5b** implements this loop. This decision does **not** require implementing the loop in the same change set as the decision record.

## Why

Operator intent: separate research from decision-making, compare evidence by signal type, expose the Claude↔Jev dialogue per ticker, and use Claude’s info-needs to plan ingest upgrades — instead of a single opaque Claude rationale + one combined Jev call.

## Spec touchpoints

- `spec/domain.md`, `spec/architecture.md`, `spec/data.md`
- `spec/behaviour/agent-advisory.md`
- `spec/tests.md` (advisory acceptance)
