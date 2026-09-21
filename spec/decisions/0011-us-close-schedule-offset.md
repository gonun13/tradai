# 0011 — Post–US-close schedule offset

Date: 2026-09-20

## Change

Lock the daily advisory clock that `0005` left as an implementation detail.

## Decision

- Timezone: **`America/New_York`** (US Eastern; observes DST).
- Regular session close: **16:00** ET.
- Fire advisory at **16:00 ET + `ADVISORY_AFTER_US_CLOSE_MINUTES`** (default **30** → **16:30 ET**).
- Skip **Saturday / Sunday**.
- Skip dates in a worker-maintained **NYSE full-day closed** list (major holidays); if a holiday is missing from the list, a run may still fire — operator can ignore or Force run later.
- At most one **scheduled** attempt per ET calendar day (marker under the data volume). Manual **Run now** does not replace the schedule; the daily cache (`ADVISORY_INTERVAL_SECONDS`) still prevents duplicate Claude/Jev spend when a fresh run already exists.
- Document the offset in `.env.example`.

## Why

Operator preference (`0005`): wait until the US session is done. Thirty minutes after the bell gives quotes/news a short settle window without waiting until evening EU time.

## Spec touchpoints

- `spec/decisions/0005-after-us-close.md`
- `spec/PROJECT.md`, `spec/architecture.md`, `spec/behaviour/agent-advisory.md`
- `spec/stages.md` Stage 6
