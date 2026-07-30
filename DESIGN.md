# Design

## Source of truth

- Status: Active
- Last refreshed: 2026-07-31
- Product: BrickerP AI Usage Chronicle
- Primary surfaces: GitHub Pages Chronicle and fixed light/dark README SVG cards.
- Data source: The existing `public/usage.json`; the presentation layer does not alter the Codex, Claude Code, Cursor, or One API collection and reconciliation rules.

## Product intent

This is a personal, public record of token traffic over time. It is not a cost-management dashboard and does not claim that token volume measures output, productivity, quality, or contribution.

The zero-interaction reading path is:

1. Identify the owner and Chronicle.
2. Read one lifetime headline: recorded tokens.
3. Understand the date span and cache share.
4. Read the all-time stacked Skyline.
5. Optionally enter Explore for tool, model, cost, and date detail.

The interactive path is:

`Select tool → view models → focus model → chart, URL, keyboard state, and accessible status update together`

## Dynamic data rules

- Never hardcode the current lifetime total, cache total, cache percentage, recorded-day count, date span, per-tool totals, or Skyline geometry.
- The lifetime hero and Skyline always derive from every published daily row.
- Explore range controls affect only the Explore summary, tool cards, model series, spend line, and detailed table.
- Recorded tokens are the sum of the four existing mutually exclusive tool token series.
- Cached context is the sum of the existing cache read/create/write fields; disclose its share beside the hero in secondary text.
- “Recorded days” means valid dates represented in the published daily ledger.
- Use “recorded tokens”, “recorded token traffic”, and “usage activity”. Do not use productivity, impact, contribution score, rank, streak, or achievement language.

## Information architecture

### Chronicle

- `BRICKERP / AI USAGE CHRONICLE`
- One dynamic lifetime token number with the label `recorded tokens`
- Dynamic first/last dates and recorded-day count
- Dynamic cached-context total and percentage, plus the non-productivity clarification
- All-time Skyline stacked by Codex, Claude Code, Cursor, and One API
- Updated timestamp; degraded sources appear as a quiet inline state

### Explore

- Dynamic visible-range summary
- Four compact tool cards with token total, cost estimate, cache/input/output breakdown, model count, and explicit selected state
- 7/30/90/All presets and progressive custom-date controls
- Existing daily token/spend chart
- Existing tool/model drilldown, Focused state, Escape unwind behavior, URL state, live-region status, and exact accessible table

### Report details

- Generated time, timezone, full span, machines, source freshness reasons, token methodology, and cost-estimate methodology
- Closed by default; the quiet source indicator opens the complete explanation

## Signature visual: AI Usage Skyline

- Desktop uses the all-time daily series.
- Viewports below 500px use natural Monday-to-Sunday weekly totals and explicitly label the weekly aggregation.
- Height is a linear recorded-token scale; segments are stacked in stable tool order.
- Selecting a tool keeps its layer saturated and mutes the other layers, then moves focus to Explore.
- Color never carries meaning alone; labels and accessible summaries identify every tool and aggregation unit.
- No gradients, glow, glass, 3D, particles, contribution heatmap, badges, milestones, or decorative animation.

## Visual language

- Canvas: `#F4F5F7`
- Ink: `#17191F`
- Tool categories retain stable blue/orange/teal/purple colors across the hero Skyline, Explore, detailed chart, and README cards.
- System sans for interface copy; system monospace with tabular numerals for the hero, dates, and compact metadata.
- Flat surfaces, thin neutral rules, compact controls, and no ornamental elevation.
- The lifetime number is the only Hero-scale number. Cache and cost figures remain supporting or Explore-level information.

## States and accessibility

- Loading: render a stable Hero + Skyline skeleton matching the final layout; one polite busy status; decoration is hidden from assistive technology.
- Full load error: retain a prominent assertive alert and Retry action.
- Empty data: retain a readable status and Reload action.
- Degraded source: quiet inline metadata near Updated, without a live alert; complete sanitized reasons remain in Report details.
- Selected tool and Focused model use visible labels plus `aria-pressed`; focus is restored when details open/close.
- Tool/model series support arrow-key movement; Escape closes model details, clears model focus, then clears tool selection one layer at a time.
- Respect `prefers-reduced-motion`.
- Target WCAG 2.1 AA contrast and no horizontal overflow at 390px.

## README cards

- Build two deterministic static assets:
  - `ai-usage-card-light.svg`
  - `ai-usage-card-dark.svg`
- Fixed 560×160 viewport.
- Derive the recorded-token total, first/last dates, cache total/share, and weekly stacked Skyline from `public/usage.json`.
- Include owner identity and an accessible SVG title/description.
- Exclude spend, models, machines, ranking, controls, configurable themes, and dynamic endpoints.
- Link the image to the complete GitHub Pages Chronicle with a `<picture>` element.

## Implementation constraints

- Framework: existing Astryx + React + TypeScript + CSS frontend; Python collection and merge scripts remain authoritative for data.
- The SVG generator runs before each production build; Vite copies the generated public assets into `docs/`.
- Scheduled publication continues to use the isolated publisher clone and stages generated report artifacts only.
- Collection ownership remains unchanged:
  - Codex / Claude Code: per-machine durable fragments, summed across machines.
  - Cursor: account-level series, never summed across machines.
  - One API: residual non-GPT/Codex and non-Claude gateway series with complete-window replacement and retained prior history on incomplete refresh.
  - Historical local Comate data remains under One API only where gateway coverage is absent.
- Do not infer authentication identity from Git credential usernames; publication still requires a non-mutating write-access probe and uses the ordinary credential helper.

## Verification contract

- Pure tests cover lifetime derivation, cache ratio, natural-week aggregation, tool emphasis, URL default semantics, deterministic SVG output, XML safety, and model-series conservation.
- Production verification runs the frontend tests, lint, TypeScript/Vite build, and `git diff --check`.
- Visual QA covers desktop and 390px mobile layouts, all-time default, daily/weekly labels, no overflow, source/loading/error hierarchy, and the complete tool → model → Focused synchronization.
- The generated light/dark SVGs must parse as XML and be present in `docs/`.

## Non-goals

- Productivity or contribution scoring
- Rankings, streaks, achievements, milestones, heatmaps, Wrapped, or Recap
- Budgets, forecasting, billing reconciliation, or provider-efficiency comparisons
- A dynamic README card service or theme/configuration API
- Team/multi-user dashboards
- Changing collection or token-accounting semantics for storytelling

## Open questions

- If Cursor later enables a custom OpenAI-compatible key, identify its gateway and add request-level ownership evidence before treating One API as non-overlapping.
- If One API exposes a stable client/application field, prefer it over model-family ownership rules.
