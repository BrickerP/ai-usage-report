# Step 1 — Brainstorm

## Hypothesis

**AI Usage: The Endless Run** can make a personal usage report easier to explore by presenting the existing data as an optional journey through an original pixel 2D data world. Confidence: **85%**. The uncertainty is whether the guide helps discovery without slowing down people who already know the dashboard.

## How might we

How might we turn a truthful, all-time AI usage record into a memorable exploration while preserving its exact data meaning, direct-access behavior, and accessibility?

## Audience and success

- Primary user: the owner or a visitor who wants to understand usage across Codex, Claude Code, Cursor, and One API.
- Not for: people seeking productivity scores, competition, rewards, or a playable platform game.
- Success means a first-time visitor can move from the full record to a tool, a model, and the real run record, then return or replay without losing URL, keyboard, or assistive-technology context.
- Hard constraints: real published usage only; All time remains the default; the guide is optional and non-modal; no collection or accounting changes.

## Variants considered

1. **Static pixel reskin** — memorable, but does not improve exploration.
2. **Mandatory tutorial** — explains the page, but blocks direct use and deep links.
3. **Optional guided run** — follows real selections while Free Roam stays available.
4. **Full platform game** — visually loud, technically expensive, and distorts the report.
5. **Record-only reveal** — creates one strong moment, but leaves tool/model discovery weak.
6. **Multiple themed worlds** — flexible, but fragments the single data story.

## Direction comparison

| Direction | User value | Feasibility | Difference | Hidden assumption |
| --- | --- | --- | --- | --- |
| Static report refresh | Faster visual polish | High | Low | Appearance alone fixes discovery |
| Optional guided run | Adds a clear path without blocking exploration | High | High | The guide can stay subordinate to the data |
| Playable game | Novel spectacle | Low | High | Game mechanics would not imply false achievement |

## Recommendation

Choose the **optional guided run**:

`Free Roam → Loadout → Model Focus → Reveal real Run Record → Run Complete → Replay`

It adds one coherent journey while leaving the report immediately usable. The guide observes the existing tool/model state instead of creating a second source of truth.

## Assumptions to validate

- “Free Roam” is understood as normal report browsing, not a separate mode.
- “Run Record” is read as a real data record, not a score.
- The guide remains discoverable without a modal, focus trap, or forced scroll.
- Pixel styling can remain legible on mobile and under reduced motion.
- Replay can reset only the guide while preserving canonical URL/filter behavior.

**Decision:** this direction is the approved basis for the current design round.
