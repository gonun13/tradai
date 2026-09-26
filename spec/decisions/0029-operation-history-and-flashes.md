# 0029 — Operation history modals and top-bar outcome flashes

## Decision

Operational detail leaves the Portfolio and Tracker content panels. Both modules expose the same
two bounded history surfaces: **Advisory logs** beside recommendations and **Ingest logs** beside
the agent-context preview. Each opens an accessible modal, lists the newest 20 operations, selects
the newest initially, and loads detail only for the selected row.

Manual ingest receives its own durable run record (`running`, then `succeeded` or `failed`) with
trigger and timestamps, report payload, and error. Only manual **Ingest now** requests are recorded;
scheduled worker ingestion is not. The newest 20 ingest records are retained. The existing ingest
report singleton remains the current-status and elapsed-time source.

Agent runs remain permanently stored because recommendations and alerts refer to them. Their
history list is limited to 20 and contains operational summary fields only; research and full
context are excluded. Advisory log detail contains metadata, error, and persisted `log_text`.
Forced and targeted runs are labelled from their stored runtime references.

Operation completion is reported in a status-aware flash attached to the neutral utility bar.
Success clears after four seconds; warning and error outcomes clear after eight. Module panels and
ticker log pages do not render global operation messages, ingest reports, worker logs, or run errors.
CRUD and page-loading feedback stays local.

## API contract

- `GET /ingest/runs` and `GET /ingest/runs/{id}`
- `GET /agent/runs` returns lightweight newest-20 summaries
- `GET /agent/runs/{id}/log` returns operational log detail
- Existing current-run polling, recommendation, ticker-conversation, and ingest-report APIs remain.

## Rationale

Recommendations and cached context are product content; execution diagnostics are operator history.
Separating them keeps both books calm while retaining inspectable failure and materiality evidence.
