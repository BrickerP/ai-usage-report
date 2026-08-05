# AI Usage: The Endless Run

## Source of truth

- Status: **Active design; implementation and pipeline verification pending**
- Last refreshed: 2026-08-05
- Product: **AI Usage: The Endless Run**
- Primary surfaces: the GitHub Pages report and its deterministic light/dark README SVG cards.
- Creative record: `docs/creative/endless-run/01-brainstorm.md` through `06-dogfood.md`.
- This file supersedes the deleted Pixel Night Run prototype and MVP prompt. There is no compatibility path for that obsolete concept.

## Product intent

The product is a truthful personal record of AI token traffic presented as an original, generic pixel 2D data world. It is a report with an optional guided journey, not a game and not a measurement of output, productivity, quality, or contribution.

The normal report remains immediately available as **Free Roam**. A visitor who wants orientation can follow one non-modal loop:

`Free Roam → Loadout → Model Focus → Reveal real Run Record → Run Complete → Replay`

The loop helps visitors understand the relationship between the all-time history, tools, models, and the real record. It never gates data, traps focus, or creates fictional progress.

## Experience contract

### Free Roam

- The complete report is usable without starting or completing the guide.
- All time remains the default range and retains the existing default-URL semantics.
- Direct links to supported tool, model, focus, and range states continue to open in their canonical state.

### Guided loop

| State | Meaning | Contract |
| --- | --- | --- |
| Free Roam | Normal report browsing | Guide is optional and inline |
| Loadout | Select Codex, Claude Code, Cursor, or One API | Existing tool selection is authoritative |
| Model Focus | Inspect and focus a real model | Chart, URL, visible state, keyboard focus, and ARIA status synchronize |
| Reveal real Run Record | Emphasize the exact peak record from published usage | Show real date, value, and units; never call it a score |
| Run Complete | Acknowledge that the route was traversed | No XP, level, reward, achievement, rank, or generated value |
| Replay | Begin another guided traversal | Reset guide-only progress without replacing canonical filter/URL rules |

Guide progress should be derived from the existing selection and focus state wherever possible. Do not create a second source of truth for tool or model state.

## Data invariants

- The existing `public/usage.json` remains the presentation data source. The Python collection and merge pipeline remains authoritative.
- Never hardcode current totals, cache values or ratios, dates, recorded-day count, record day, per-tool values, model values, costs, or chart geometry.
- Recorded tokens remain the sum of the four existing mutually exclusive tool series: Codex, Claude Code, Cursor, and One API.
- Collection ownership remains unchanged:
  - Codex and Claude Code use durable per-machine fragments summed across machines.
  - Cursor remains an account-level series and is never summed across machines.
  - One API remains the residual gateway series, retaining prior history when a refresh window is incomplete.
  - Historical local Comate data remains under One API only where gateway coverage is absent.
- Cached context, cost estimates, source freshness, dates, and token-part methodology retain their existing meanings and disclosures.
- Explore range controls affect only their existing visible-range surfaces; they do not rewrite lifetime facts.
- Usage language must remain factual: “recorded tokens”, “recorded token traffic”, “usage activity”, and “Run Record”.

## Information architecture

### All-time record

- Owner/product identity and one dynamic lifetime recorded-token headline.
- Dynamic date span, recorded-day count, cached-context amount/share, and non-productivity disclosure.
- A data-derived all-time pixel route showing the four tools in stable order.
- Updated time and quiet degraded-source state linked to complete report details.

### Explore and guided states

- Existing range summary, four tool controls, model inspection/focus, token and spend charts, exact table, and report details remain available.
- Loadout and Model Focus rename the journey stage, not the underlying data or controls.
- The real Run Record remains accessible outside the guide; “Reveal” is narrative emphasis, not an access gate.
- Report details retain timezone, full span, machines, source-freshness reasons, token methodology, and cost-estimate methodology.

### README cards

- Continue generating deterministic `ai-usage-card-light.svg` and `ai-usage-card-dark.svg` assets from `public/usage.json`.
- Preserve their fixed 560×160 viewport, accessible title/description, recorded-token total, date span, cache disclosure, and weekly stacked history.
- README cards do not implement the guided loop, interactive controls, game mechanics, or a dynamic card service.

## Visual language

- Subject: an **original generic pixel 2D data world**, with no identifiable real-world route.
- Signature element: one continuous pixel route shaped by real usage and current selection.
- Palette: night navy `#0B1320`, slate `#1D2A38`, paper `#E7EDF3`, signal gold `#F2BD4B`, focus cyan `#48C7C1`, and alert coral `#E0645A`.
- Use readable interface type for explanation and monospace/tabular numerals for dates, tokens, and route labels.
- Pixel treatment belongs to edges, route marks, small environmental shapes, and headings. Exact data remains crisp, labelled, and readable.
- Color never carries state alone.
- Do not use Beijing Second Ring Road, a recognizable skyline, copied game characters or worlds, console hardware, borrowed sprites, sounds, palettes, or trade dress.

## States, accessibility, and responsive behavior

- The guide is inline and non-modal: no overlay, focus trap, automatic focus theft, or required auto-scroll.
- Preserve visible keyboard focus, arrow-key model navigation, focus restoration, and Escape unwind behavior.
- Preserve supported URL/deep-link state and the existing live-region announcements. Announce meaningful state changes once, without narrating decoration.
- Selected tools and focused models retain visible text plus programmatic pressed/selected state.
- Loading uses a stable layout and polite busy status; full-load error retains an assertive alert and retry; empty and degraded states remain explicit.
- Respect `prefers-reduced-motion`; remove travel animation without removing state, order, or content.
- At 390px the experience is one readable vertical route with no horizontal overflow or drag requirement.
- Target WCAG 2.1 AA contrast.

## Implementation boundaries

- Use the existing Astryx, React, TypeScript, CSS, and chart stack.
- Do not add Canvas, a game engine, physics, collision, a custom rendering runtime, or a speculative dependency.
- Do not change collection, reconciliation, history retention, caching, pricing, publication, or README-card ownership for the narrative layer.
- Remove obsolete Pixel Night Run paths rather than retaining fallbacks, aliases, migration behavior, or duplicate copy.
- Prefer the smallest vertical slice that completes the six-state loop with real data.

## Verification and pipeline contract

No local build, test, lint, formatting, visual QA, or Git verification was run for this documentation update. The following are required remote-pipeline evidence, not current pass claims:

- `npm run test:frontend`
- `npm run lint`
- `npm run build`, which runs README-card generation, `tsc -b`, and the Vite production build
- the repository diff/whitespace gate used by the pipeline
- desktop and 390px visual review of Free Roam and the complete guided loop
- keyboard-only, reduced-motion, loading, error, empty, degraded-source, URL, and ARIA review
- data comparison proving totals, cache, costs, dates, tool/model ownership, and Run Record still derive from the published ledger
- generated light/dark SVG XML parsing and presence in the published `docs/` output

The evaluation plan and actual result ledger live in `docs/creative/endless-run/06-dogfood.md`. Do not mark the feature complete until pending evidence is replaced by observed results.

## Non-goals

- XP, levels, rewards, achievements, ranks, streaks, badges, milestones, or scores
- Productivity, contribution, impact, or quality claims
- A playable platformer, avatar, combat, physics, collision, or game simulation
- Beijing Second Ring Road or any identifiable city journey
- Canvas or a game engine
- Budgets, forecasting, billing reconciliation, or provider-efficiency comparison
- Team or multi-user dashboards
- Changes to usage collection or token-accounting semantics
- Multiple guided routes, alternate game themes, or decorative mechanics without dogfood evidence
