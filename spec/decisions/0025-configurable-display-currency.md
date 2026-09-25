# 0025 — Configurable display currency

Date: 2026-09-25

## Change

Replace the fixed-EUR presentation rule with one global display-currency preference.

## Decision

- The operator selects `EUR`, `USD`, `GBP`, or `CHF` on `/setup`; default is `EUR` for new and existing installations.
- Instrument and quote currencies remain native facts. The preference changes converted presentation and new advisory calculations only; it is never used to infer a listing currency or region.
- Cash and the realised-gains override are source-aware `{ amount, currency }` values. Changing display currency does not change their source currency. Legacy EUR settings migrate once as EUR.
- Frankfurter rates remain cached as `base → EUR`. A conversion to display currency uses the EUR pivot: `base_to_eur / display_to_eur`.
- FX refresh covers instrument currencies, quote currencies, both monetary-setting source currencies, and all four supported display currencies.
- A converted value with missing required FX is `null` / **pending FX**. Portfolio aggregates and weights are unavailable when any constituent conversion is unavailable; native values must never be relabelled.
- Live read models and new agent contexts use currency-neutral `_display` names. Every response states `display_currency`.
- Each new AgentRun snapshots the selected currency. Historical EUR snapshots keep their original `_eur` fields and render as EUR without mutation.

## Supersedes

This decision supersedes the fixed-EUR and “no multi-currency UI” parts of `0003-display-eur`.
It does not change `0006`: Frankfurter remains the auxiliary FX source and EUR remains the cached pivot.

## Consequences

- Setup has a neutral currency form independent of the Portfolio and Tracker forms.
- Converted portfolio cost/value/P&L, Tracker price, cash, realised gains, totals, concentration, and advisory inputs use the selected currency.
- Native quote prices, acquisition prices, transaction previews, raw fundamentals, percentages, and technical indicators remain unchanged.
