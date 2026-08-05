# Step 1 — Brainstorm

## Hypothesis

**AI Usage: Run the Archive** can turn a personal usage history into a memorable, truthful interaction by making the existing timeline itself the level. Each focused model generates a deterministic map and one history-derived **Run Signature**. Confidence: **96%**.

The previous optional guide was rejected after live dogfooding: it repeated the already-visible tool/model state, added a redundant Reveal action, and placed a fixed runner animation beside a second archive summary.

## How might we

How might we let a visitor explore a model's real recorded history as a distinctive 2D level, while keeping every visible claim traceable to the published usage data and preserving direct URL, keyboard, chart, and assistive-technology behavior?

## Audience and success

- Primary audience: a visitor to BrickerP's GitHub profile who wants a memorable glimpse of a long-running personal AI history.
- Secondary audience: the owner comparing how different tools and models appeared across time.
- Not for: people seeking model benchmarks, productivity judgment, competition, rewards, failure states, or a full platform game.
- Success means that selecting two different models visibly produces two different traversable histories without another Reveal, Replay, or route-selection control.
- Hard constraints: published data only; All time remains the default; no collection or accounting change; no invented model intelligence, XP, score, rank, or reward.

## Variants considered

1. **Longer fixed runner animation** — more spectacle, but still passive and disconnected from the data.
2. **Single Run button** — fewer controls, but still adds an artificial gate before already-known data.
3. **Scroll-driven cinematic** — low input cost, but risks feeling like scroll hijacking.
4. **Chart-as-level** — date exploration and gameplay become the same interaction.
5. **Procedural data loom** — visually original, but abandons the approved pixel side-scrolling language.
6. **Vendor capability classes** — recognizable, but requires curated external claims and can imply a misleading model ranking.
7. **Personal-history Run Signatures** — distinctive per model, deterministic, and fully attributable to the owner's records.

## Direction comparison

| Direction | User value | Feasibility | Difference | Hidden assumption |
| --- | --- | --- | --- | --- |
| Ghost Run cinematic | Immediate showpiece | High | Medium | Watching alone feels playable |
| Trace Loom | Strong data-art identity | Medium | High | Abandoning the current world is acceptable |
| **Run the Archive** | Exploration and exact history become one action | High | High | A small set of honest traits can create enough variety |

## Approved direction

Choose **Run the Archive: the chart is the level**.

`Select tool → Focus model → map regenerates → traverse real dates → pass the real Record → arrive at Last Seen / Now`

- Remove the independent Run Stage, fixed runner route, Reveal, Replay, Choose another route, and Lifetime Archive toggle.
- Map X position comes from dates; terrain comes from daily model tokens; gaps come from inactive dates; landmarks come from first seen, cumulative midpoint, peak, and last seen.
- Mouse, touch, and Left/Right keys move through the same real daily series used by the chart and exact table.
- Selecting another model is the only route-change action.
- Lifetime peak, recorded days, and tools become a compact always-visible save strip rather than a second results panel.

## Run Signature rule

“Ability” means a visible pattern in this owner's recorded history, not an intrinsic capability of the AI model.

- Each model receives at most one explainable Run Signature.
- The signature uses only typed, cross-tool history fields: daily tokens, active dates, first/last observed dates, and peak. Cost remains an exact readout, not a trait input.
- The UI exposes the evidence window and reason for the trait.
- Missing or insufficient history produces a neutral map and `History still forming`, never an inferred negative trait.
- The same history and rule version always generate the same map and signature.

Candidate signatures for prototype testing: `Sprinter`, `Marathon`, `Hopper`, `Climber`, `Pulsar`, with a neutral `Trailblazer` fallback. Names and thresholds remain provisional until the prototype step.

## Assumptions to validate

- A visitor understands Run Signature as personal-history behavior rather than a benchmark.
- Model maps remain visually distinct at All time without hiding exact daily values.
- Date traversal can reuse the existing chart keyboard contract without introducing an application role or page-scroll conflict.
- A single Record landmark provides enough payoff when the journey continues to Last Seen / Now.
- The compact save strip can replace the Lifetime Archive without losing a useful fact.

**Decision:** the user approved personal-history Run Signatures instead of vendor capability claims.
