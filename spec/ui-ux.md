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
- Setup is neutral. Decision logs inherit the module that produced the recommendation.

## Module identities

Both modules use the same typographic family, spacing scale, form controls, table behavior and
shared components.

| Module | Character | Visual cues | Primary content order |
| --- | --- | --- | --- |
| Portfolio | Precise ledger | Evergreen on warm ivory, square rules, owned-capital language | Summary, holdings, decisions, agent context |
| Tracker | Exploratory research workspace | Cobalt on cool mist, softer research surface, entry language | Summary, discovery, tracked names, signals |

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

## Responsive and accessible behavior

- The utility bar may wrap on medium screens and stacks above the module on narrow screens.
- Wide data tables scroll horizontally without clipping actions or changing column meaning.
- Module switching, shared tools, forms, alert tabs and log links are keyboard reachable with a
  visible focus indicator.
- Active module state uses `aria-current="page"`; controls keep explicit labels and disabled state.
- Text and interactive boundaries must remain readable at WCAG AA contrast in both palettes.

