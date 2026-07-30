# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-07-30
- Primary product surfaces: Static usage dashboard, daily stacked charts, tool summary cards.
- Evidence reviewed: `README.md`, `src/lib/usage.ts`, `src/components/UsageCharts.tsx`, `src/index.css`, `scripts/ai_usage_comparison_image.py`, and the published GitHub Pages report.

## Brand
- Personality: Calm, precise, personal engineering telemetry.
- Trust signals: Explicit source boundaries, visible date ranges, stable totals, and honest cost-estimate language.
- Avoid: Decorative complexity, unexplained double counting, and presenting estimates as billed amounts.

## Product goals
- Goals: Show a readable daily and historical view of AI usage across Codex, Claude Code, Cursor, and the residual One API source, including the models that compose each existing card.
- Non-goals: Reconcile provider invoices, attribute every request to an editor process, or claim exact USD settlement for One API.
- Success signals: Existing Codex/Claude/Cursor series remain stable; Comate is no longer independent; model rows reconcile to each card; combined totals contain mutually exclusive Codex, Claude, Cursor, and residual One API traffic.

## Personas and jobs
- Primary personas: The repository owner using multiple Macs and publishing a private-operations-style public report.
- User jobs: Compare tools, inspect daily token composition, estimate spend, and detect collection gaps.
- Key contexts of use: Desktop overview, mobile spot checks, scheduled multi-Mac publishing, and local debugging.

## Information architecture
- Primary navigation: Single-page report with date controls.
- Core routes/screens: One dashboard route backed by `public/usage.json`; no model-specific route or secondary dashboard.
- Content hierarchy: Overall range and totals, tool cards, daily token/spend chart, then methodology notes.

## Design principles
- Principle 1: Source ownership must be understandable before values are combined.
- Principle 2: Preserve last known good account-level history when a refresh is incomplete.
- Tradeoffs: Model-family filtering is intentionally conservative; empty or unknown model names are omitted rather than guessed.

## Visual language
- Color: Keep existing tool colors; One API remains purple (`#7c3aed`).
- Typography: Reuse the current Astryx/React typography and tabular number treatment.
- Spacing/layout rhythm: Reuse current card, chart, and control spacing.
- Shape/radius/elevation: Reuse existing cards and subtle elevation.
- Motion: Reuse chart interactions; do not add decorative motion.
- Imagery/iconography: Data-first interface; no additional imagery required.

## Components
- Existing components to reuse: Tool cards, usage charts, range controls, methodology copy.
- New/changed components: Existing tool cards gain compact model rows. Comate is removed as a card and series; its local pre-gateway history is retained under One API.
- Variants and states: Fresh source data, stale prior data, missing session, incomplete pagination, and zero residual traffic. Source degradation appears as one compact page-level notice; healthy reports add no new UI.
- Token/component ownership: Existing CSS and `src/lib/usage.ts` own visual tokens; Python owns collection, per-day model persistence, and merge semantics.

## Accessibility
- Target standard: Preserve current semantic HTML and aim for WCAG 2.1 AA readability.
- Keyboard/focus behavior: Existing range controls must remain keyboard operable.
- Contrast/readability: Preserve existing high-contrast text and distinguish One API with both label and color.
- Screen-reader semantics: Tool names and numeric labels must not rely on color alone.
- Reduced motion and sensory considerations: No new animation; respect existing chart behavior.

## Responsive behavior
- Supported breakpoints/devices: Current desktop and mobile breakpoints.
- Layout adaptations: Tool grid wraps using the existing responsive rules.
- Touch/hover differences: Essential values remain visible without hover; tooltips are supplementary.

## Interaction states
- Loading: Static JSON loads through the existing application flow.
- Empty: A source may show zero without removing the card.
- Error: A failed account-level refresh keeps prior daily values and records stale/error metadata in `usage.json.source_status`.
- Success: Only a complete fetched window replaces the same dates in prior One API history.
- Disabled: Not applicable.
- Offline/slow network, if applicable: Build uses the last successfully persisted One API series when authentication or pagination fails.

## Content voice
- Tone: Direct, technical, and evidence-based.
- Terminology: “One API” means residual gateway traffic; “cost” for One API is a USD estimate derived from quota.
- Microcopy rules: State exclusions and staleness plainly; never describe overlapping sources as a combined total.

## Implementation constraints
- Framework/styling system: Existing Astryx + React + TypeScript + CSS frontend; Python collection and merge scripts.
- Design-token constraints: Extend existing tool metadata and colors; do not add another design-system layer.
- Performance constraints: Fetch all One API pages in one browser evaluation, with bounded retry for rate limits.
- Compatibility constraints: UUAP requires a saved `chrome-use` browser state with httpOnly cookies; the current Cursor account must not use a custom OpenAI key for the non-overlap assumption.
- Publication constraints: Scheduled refreshes run from an isolated publisher clone, never abort or switch a developer's Git state, and stage generated report artifacts only.
- Test/screenshot expectations: Unit-test classification, quota conversion, browser-state use, incomplete-fetch rejection, and durable history reconciliation; run lint and production build.

## Open questions
- [ ] If Cursor later enables a custom OpenAI-compatible key, identify its gateway and add request-level ownership evidence before treating One API as non-overlapping.
- [ ] If One API exposes a stable client/application field in future, prefer it over model-family ownership rules.
