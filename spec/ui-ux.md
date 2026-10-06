# UI / UX

Audience: product, design and frontend implementation.

## Application structure

Portfolio and Tracker are separate modules in one Tradai application (`0021`). They share data
services, formatting, controls and one operational toolset, but they must not read visually as
two tabs in the same book.

- `/portfolio` is the canonical owned-capital workspace and the default destination from `/`.
- `/tracker` is the canonical research-pipeline workspace.
- A compact module switcher changes workspace. It is app navigation, not an in-page tab strip.
- Ingest, Run, Force run, Setup and Alerts live in a neutral utility bar because they operate on
  both books. Copy must make that shared scope clear.
- Ingest and Run show the elapsed time since their latest invocation directly below their controls,
  in fractional hours.
- Setup is neutral. Decision logs inherit the module that produced the recommendation.
- A concise outcome flash appears at the utility bar after ingest or advisory completes. Success
  remains for four seconds; warnings and failures remain for eight seconds.

## Module identities

Both modules use the same typographic family, spacing scale, form controls, table behavior and
shared components.

| Module | Character | Visual cues | Primary content order |
| --- | --- | --- | --- |
| Portfolio | Precise ledger | Evergreen on warm ivory, square rules, owned-capital language | Summary, holdings, decisions, agent context |
| Tracker | Exploratory research workspace | Cobalt on cool mist, softer research surface, entry language | Summary, discovery, tracked names, signals, agent context |

Every module opens with its name, purpose, subject count and its own advisory horizons. Color is
supporting information only: titles, copy and active navigation also identify the module.

## Interaction rules

- `/` redirects to `/portfolio` and preserves query parameters.
- Tracker's **Bought** action opens `/portfolio?buy=<symbol>`; recording the acquisition remains
  the action that archives the tracker entry.
- Recommendation logs use `/portfolio/log/<symbol>` or `/tracker/log/<symbol>`. The legacy
  `/log/<symbol>` path redirects to the book stored on the latest recommendation.
- Global alerts show their originating book and link to that book's decision log.
- Loading, empty, warning and error states stay inside the active module identity.
- Both modules expose the next-run agent context per ticker in disclosures that are closed by
  default. An open disclosure shows Historical, Fundamentals and Technicals in one responsive
  row, followed by a full-width News row; each layer names missing data explicitly.
- Recommendations expose **Advisory logs** and Agent context preview exposes **Ingest logs**. Each
  opens its own newest-20 modal, selects the newest row, and fetches detail only on selection.
  Operation metadata, worker logs, ingest reports, and operation feedback do not appear inline in
  module panels or ticker log pages. CRUD and page-load feedback remains inline.

## Setup

`/setup` is a neutral utility page with two book-specific cards in module-switcher order, followed by a compact independent global currency form:

- **Portfolio** appears first and uses the evergreen, square-edged owned-capital identity. Its
  form contains the Portfolio profile, source-aware cash reserve and realised gains elsewhere (each amount has its own four-currency selector), the calculated
  realised total, and **Save portfolio**.
- **Tracker** uses the cobalt, softer research identity. Its form contains the Investor profile,
  the empty-profile warning, and **Save investor profile**.
- **Display currency** follows the profiles in a compact neutral row, selects EUR, USD, GBP, or CHF, and has its own loading, success, error, disabled, and save state. Saving it sends only `display_currency`; it never submits either book's unsaved draft.

Each form has independent loading, disabled, success, and error states. Saving a form sends only
that card's fields, so an unsaved draft or persisted value in the other card cannot be overwritten.
The Investor profile sets the Tracker buying lens and accompanies all advice; the Portfolio
profile adds sizing, trimming, selling, and tax rules for owned positions.

A compact, neutral **Service status** area reports Claude research and Jev decisions as **Ready**
or **Needs attention**, and Finnhub, Marketaux, and Alpha Vantage as **Connected** or **Not connected —
optional**. It may show a concise Anthropic API billing conflict warning and offers **Check again**.
It does not expose environment variable names, vendor URLs, setup commands, or credential
locations. Daily schedule and alert-policy details remain outside the Setup page.

## Responsive and accessible behavior

The Portfolio holdings table keeps each row ledger-like and compact: Symbol omits region; Quote
omits provider provenance; Cost, Value and P&L suppress duplicate native/display figures and never
display the FX rate. **Daily %** uses a subtle green, red or neutral cell background according to
sign, followed by a final column of small labelled chart/sell/edit/erase icon controls. When a
holding has realised P&L from partial sells, the P&L cell adds a muted "realised …" line (`0031`).

The Portfolio summary shows Market value, Unrealised, Realised YTD, Realised all-time and Total P&L
(`0031`). A **Closed positions** panel follows the holdings table with the same ledger styling: one row
per closed instrument (opened → closed, held days, quantity, cost, proceeds, realised P&L and %),
an expandable transaction list with per-transaction delete, and a labelled erase control. Erase
copy states that it removes the whole history and that exiting is a sell.

The Tracker table uses the same **Daily %** sign treatment. Tracker has no operator-note field or
column; its per-name Run and Remove actions are compact icons with explicit accessible labels and
tooltips, while Bought remains a text action.

Every Portfolio and Tracker row also has a labelled chart icon. Activating it expands a shared,
lazy-loaded SVG performance chart below that row; only one row in a table is open at once. The
chart defaults to 5Y and offers 1Y, 2Y, 5Y, and Max. It shows indexed stock and regional-benchmark
performance, a solid/dashed labelled legend (not color alone), as-of and comparison-start dates,
endpoint returns, native currencies, and loading/empty/stale/benchmark-warning states. Range and
toggle controls expose keyboard focus, `aria-expanded`, and explicit accessible labels. On narrow
screens the chart remains legible in a horizontally contained surface.

- The utility bar may wrap on medium screens and stacks above the module on narrow screens.
- Setup's Portfolio and Tracker cards sit side by side on desktop and stack in the same order on
  narrow screens, with fields contained within their cards and each identity retained.
- Wide data tables scroll horizontally without clipping actions or changing column meaning.
- Module switching, shared tools, forms, alert tabs and log links are keyboard reachable with a
  visible focus indicator.
- Active module state uses `aria-current="page"`; controls keep explicit labels and disabled state.
- Agent-context ticker disclosures are keyboard operable and their three data columns stack without
  changing meaning on narrow screens.
- Text and interactive boundaries must remain readable at WCAG AA contrast in both palettes.
- History modals trap focus, close on Escape or an explicit Close control, restore opener focus,
  and provide loading, empty, fetch-error, and single-column narrow-screen states.
