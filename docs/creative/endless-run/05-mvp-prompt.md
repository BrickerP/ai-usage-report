# Step 5 — MVP implementation contract

## Prompt Architect

- **Problem:** visitors lack one obvious route from the all-time overview to tool, model, and record detail.
- **Target user:** a first-time or returning visitor to the personal AI usage report.
- **MVP goal:** add the optional, non-modal Endless Run loop end to end while keeping the report’s real data and existing analysis behavior authoritative.
- **Core journey:** `Free Roam → Loadout → Model Focus → Reveal real Run Record → Run Complete → Replay`.
- **Technical posture:** use the existing React, TypeScript, CSS, and chart dependencies; add no game or rendering framework.

## Non-negotiable invariants

- `public/usage.json` and the existing collection/reconciliation pipeline remain the data source of truth.
- Codex, Claude Code, Cursor, and One API keep their current mutually exclusive accounting semantics.
- Totals, cache fields, cost estimates, dates, tool/model ownership, and degraded-source meaning do not change.
- All time remains the default, including current default-URL semantics.
- Tool/model selection, Focused state, chart, URL, keyboard behavior, focus management, Escape unwind, and ARIA status remain synchronized.
- Loading, error, empty, mobile, contrast, and reduced-motion behavior remain accessible.

## Scope Cop

| Keep | Cut | Defer until evidence |
| --- | --- | --- |
| One inline optional guide | Mandatory tutorial or modal | Additional routes or tutorial variants |
| Six-state loop | XP, levels, rewards, ranks, streaks, achievements, or scores | Ambient motion beyond basic state transitions |
| Exact data-derived Run Record | Fictional progress or generated records | Extra pixel scenery that does not aid orientation |
| Original generic pixel 2D data world | Beijing Second Ring Road or recognizable geography | Any second narrative loop |
| Existing DOM/CSS/chart implementation | Canvas, game engine, physics, collision, new rendering runtime | New dependencies, only if future evidence proves necessary |
| Existing URL, keyboard, and ARIA contracts | Compatibility layer for the deleted prototype | Alternate themes or game metaphors |

## Builder contract

1. Keep Free Roam as the default, complete report experience.
2. Derive guided progress from existing tool/model state wherever possible.
3. Present the four existing tools as Loadout without changing their meaning.
4. Reuse the existing model-focus state and its URL/accessibility synchronization.
5. Derive Run Record from published usage with exact date, value, and units.
6. Make Run Complete a neutral route acknowledgement.
7. Make Replay reset guide-only progress and return to an understandable starting point.
8. Remove obsolete Pixel Night Run paths rather than adding compatibility or fallback behavior.

## Acceptance checklist

- [ ] Free Roam works without starting or completing the guide.
- [ ] The full six-state loop works end to end with mouse.
- [ ] The same loop works with keyboard, visible focus, and expected Escape behavior.
- [ ] Tool, model, chart, URL, and ARIA state remain in sync.
- [ ] All time remains the initial range when no range is encoded.
- [ ] Run Record is derived from published data and uses no score language.
- [ ] Mobile is a readable vertical journey without horizontal overflow.
- [ ] Reduced motion preserves every state without animated travel.
- [ ] No Beijing route, borrowed game identity, XP, level, reward, score, Canvas, or game engine is present.
- [ ] Existing real usage values and four-tool accounting are unchanged.

## Pipeline evidence required

The implementation is complete only after the remote pipeline reports the relevant checks. This document does **not** claim they have run or passed:

- `npm run test:frontend`
- `npm run lint`
- `npm run build` (README-card generation, TypeScript build, and Vite production build)
- repository diff/whitespace gate used by the pipeline
- desktop and mobile visual/accessibility review of the complete loop

## Reviewer guardrail

Reject any implementation that improves the metaphor by weakening data truth, direct access, URL semantics, keyboard behavior, or ARIA behavior. Remove scope outside the Keep column instead of generalizing it.
