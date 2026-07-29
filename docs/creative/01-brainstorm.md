# Brainstorm: model visibility without a new dashboard

## Working hypothesis

The report already has the right information hierarchy. The missing piece is not
another analytics surface, but a compact explanation of which models make up each
existing tool total.

## Approved direction

- Keep the combined summary, tool cards, date controls, and charts in place.
- Keep Codex, Claude Code, and Cursor accounting unchanged.
- Remove Comate as an independent card and series.
- Fold the one-time local Comate history into One API, without double-counting
  gateway-covered dates.
- Add compact model rows inside each existing tool card.
- Each model row shows only model name, tokens, and estimated cost.
- Model rows follow the selected date range.

## Explicit non-goals

- No model-first dashboard.
- No additional KPI vocabulary.
- No second leaderboard, drawer, or drill-down mode.
- No invoice-grade reconciliation.
- No redesign of the existing chart.

## MVP

One implementation pass: normalized per-day model breakdowns in `usage.json`,
four existing-style cards with inline model rows, migrated local Comate history,
updated favicon, production build, publication, and launchd restart.
