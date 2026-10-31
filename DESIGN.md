# The Compute Ledger

Status: implementation candidate, remote verification and publication pending.
Updated: 2026-10-31. Art direction and complete interaction design approved by the owner.

## Product and art direction

A truthful personal yearbook of recorded AI usage: warm paper, ink, vermilion,
serif display typography, asymmetric editorial columns and an instrument register.
The page reports token traffic and estimated costs, never productivity, quality,
contribution or a game score.

The earlier pixel terminal, standalone skyline and runner-world interface are
replaced. Their data exploration capabilities survive as date indices, bookmarks,
model folios, pattern notes and exact records. There is one production UI, without
a legacy mode, fallback theme or migration page.

## Runtime architecture

- Svelte 5 and TypeScript handle state and semantic native controls.
- SVG charts use D3 scales and line geometry; no canvas/chart engine or game runtime.
- `src/lib/report.ts` derives the view directly from the existing `UsagePayload`.
- `src/lib/pattern.ts` retains the original evidence-based Signature v1 rules without
  terrain, avatar or camera geometry.
- `loadUsage()` reads the current relative `usage.json`, revalidating the response.
  Production never embeds the prototype dataset or snapshot numbers.
- Vite emits `docs/`; those files are generated build output, not UI source.

The React/Astryx/ECharts frontend and its obsolete modules and source-string style
contracts are removed. The separate Python offline report renderer remains called by
the collector's `--out` / `--png` flow and is outside this frontend replacement.

## Reading and interaction contract

### Current scope and calendar window

The headline labels its scope and displays its exact recorded tokens, estimated
cost and cache share. Tool/model selection changes the headline with the scope
label; the instrument register retains all-tool and per-tool window totals.
Model cache and categories remain explicitly tool-level where the source lacks
model components.

All remains the no-parameter default. The interface supports 7/30/90 calendar days,
custom From/To dates, Earlier/Later windows and Reset. Calendar days and published
record days are separate counts. Custom dates are applied atomically and preserve
unpublished dates at either edge, including windows containing only missing rows.

### Tool and model folios

One instrument register selects All tools or one of the four mutually exclusive
tools. Selecting a tool clears a previous model/day selection. Models retain the
existing top-four, conserving Other group and complete index. The complete index
shows exact tokens, share and cost; identified models can be focused, legacy
unattributed rows cannot. A focused model outside the top four remains visible.

All tools stacks tool series. One tool stacks its model groups. An exact model
uses the same chart and date index with its own token and cost values. There is
no replacement world or separate model dashboard.

### Charts and date index

Daily token columns have a continuous, zero-based linear scale. Estimated costs
use an aligned, separately labelled USD subplot. Series retain text labels and
stable colors. Real record days have factual labels; no split coordinate bands,
decorative bars or transformed totals are used.

Mouse hover previews exact values. A deliberate pointer selection, the date slider,
previous/next date controls and First/Record/Last bookmarks select an exact day.
Vertical touch scrolling must not commit a new date. Keyboard arrows and Home/End
remain usable. The inspector exposes the date, state, exact token/cost values,
source/model breakdown and available tool-level token parts.

Pattern notes preserve Signature v1 precedence, minimum history/coverage rules and
evidence. No new qualitative claims are inferred from decoration.

### Exact records and publication notes

The exact ledger contains the entire dense calendar, paged in batches of 50 to keep
mobile and long-history browsing manageable. Every date remains reachable.

One publication-notes section contains generated time, timezone, machine list,
full span, tool coverage, source status/window/lag/attempt/success details and
token/cost/merge methodology. Loading, retryable errors, empty windows, unpublished
dates and stale/failed sources have explicit states.

## Data semantics

- The existing collector, reconciliation, retention, frozen history, pricing and
  publication mechanisms remain authoritative and unchanged by the UI.
- Four-tool fields are summed for all-tool totals; stale `row.total_*` values do
  not override those mutually exclusive fields.
- Unpublished dates and missing model arrays remain null/unavailable. A published
  empty model array is a covered zero, not a missing record.
- A wholly unavailable scope has unavailable aggregate tokens/cost/cache, not a
  synthetic zero. Partial windows sum known records and disclose coverage.
- Model groups conserve tool tokens and costs; positive real attribution gaps are
  disclosed. Cost residuals at or below 1e-9 are floating-point noise, not separate
  consumption or fake Unclassified rows.
- Codex reasoning is part of output. Displayed non-reasoning output subtracts it
  before categories are combined. Cache read/write/create retain source meanings.
- Headline money uses two decimals, or four for nonzero amounts below one cent;
  precise records retain up to eight decimals. Calculations use unrounded values.
- README light/dark SVG cards remain deterministic 560×160 outputs of their
  independent existing generator.

## URLs and accessibility

Existing `tool/model/range/from/to/day` deep links remain canonical. Custom dates
take precedence over presets. The selected date can be inspected in every scope;
only an identified model's explicitly committed date is written as `day`. An
internal default date must not leak into the URL. Unknown/invalid state is cleared
without redirecting to a second interface. Direct links do not force a scroll.

Escape closes the model index, clears model focus, then returns to All tools.
The complete model index supports arrow/Home/End navigation and focus restoration.
The date slider exposes the actual date, value and state to assistive technology;
one polite status announces meaningful scope/date changes.

At 390px the edition flows vertically, controls wrap, and tables scroll only inside
their bounded wrappers. Essential text is at least 11px; primary touch targets are
44px. Reduced motion and forced colors retain data and interaction semantics.

## Verification and deployment

No local application build, test suite, lint or preview compilation is run for this
implementation. Code is pushed to a CR and compiled in GitHub Actions.

Remote gates are collector regressions, frontend semantic tests, Svelte/TypeScript
checks, production build and Playwright against the built static site. Browser
checks independently calculate totals from published tool fields and cover all
presets, custom dates, tools/models, exact records, default/committed day deep links,
keyboard actions, missing-only windows, pagination and 1280px/390px overflow.

Build artifacts and failure evidence are retained for seven days. After the CR's
checks pass, merge to main and let the same pipeline publish GitHub Pages. Finally
verify the deployed page and its core interactions against the deployed ledger.
Deployment success must be observed before reporting publication complete.
