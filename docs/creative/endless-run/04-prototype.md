# Step 4 — Prototype

## Single core journey

```text
┌─────────────────────────────────────────────────────────────┐
│ FREE ROAM                                                   │
│ Full real report · All time · Guide is optional             │
└──────────────────────────────┬──────────────────────────────┘
                               ↓ select a real tool
┌─────────────────────────────────────────────────────────────┐
│ LOADOUT                                                     │
│ Codex · Claude Code · Cursor · One API                      │
└──────────────────────────────┬──────────────────────────────┘
                               ↓ inspect and focus a model
┌─────────────────────────────────────────────────────────────┐
│ MODEL FOCUS                                                 │
│ Existing chart, URL, visible state, and ARIA synchronize    │
└──────────────────────────────┬──────────────────────────────┘
                               ↓ continue
┌─────────────────────────────────────────────────────────────┐
│ REVEAL REAL RUN RECORD                                      │
│ Exact published record date + value; never an invented score│
└──────────────────────────────┬──────────────────────────────┘
                               ↓ acknowledge
┌─────────────────────────────────────────────────────────────┐
│ RUN COMPLETE                                                │
│ Exploration completed; no XP, level, reward, or achievement │
└──────────────────────────────┬──────────────────────────────┘
                               ↓ replay
                    FREE ROAM / begin again
```

The path is a non-modal inline guide. It never covers the report, traps focus, or prevents direct navigation. Guide progress follows the canonical tool/model state instead of duplicating it.

## State contract

| State | Visible purpose | Required behavior |
| --- | --- | --- |
| Free Roam | Read or explore normally | All time remains default; guide may be ignored |
| Loadout | Choose one of the four truthful tools | Existing selection, chart, and URL behavior remains authoritative |
| Model Focus | Inspect and focus a real model | Keyboard, Escape, focus restoration, visible label, and ARIA announcement remain synchronized |
| Reveal real Run Record | Highlight the exact data-derived record | Preserve date, units, and underlying access; never rename it score |
| Run Complete | Confirm the route was traversed | No reward language or generated value |
| Replay | Offer another traversal | Reset guide-only state; preserve canonical URL/filter rules |

## Design brief

- **Subject:** a personal AI usage history represented as an original generic pixel 2D data world.
- **Audience:** visitors who want orientation plus returning users who want direct analysis.
- **Page’s one job:** make the relationship between all-time record, tool, model, and real run record obvious.
- **Palette:** night navy `#0B1320`, slate `#1D2A38`, paper `#E7EDF3`, signal gold `#F2BD4B`, focus cyan `#48C7C1`, alert coral `#E0645A`.
- **Type roles:** existing readable interface sans for explanation; monospace/tabular numerals for dates, tokens, and route labels.
- **Signature element:** a continuous pixel route whose segments are shaped by real usage data and current selection.

## Visual boundaries

- Pixel treatment is limited to edges, route marks, small environmental shapes, and headings; numerical data remains crisp and explicit.
- The world is original and geographically generic: no Beijing Second Ring Road, recognizable skyline, commercial game world, console shell, character, or borrowed sprite language.
- Use the existing DOM/CSS/chart stack. No Canvas, game engine, physics, collision, or gameplay simulation.
- Color is never the only state signal. Every state has text and programmatic state.
- Mobile becomes one vertical route with no horizontal drag requirement.
- Reduced motion removes travel animation while retaining the same final states and reading order.

## Approval gate

This journey and design brief are the accepted implementation boundary. Anything outside it requires a new design decision rather than incidental expansion.
