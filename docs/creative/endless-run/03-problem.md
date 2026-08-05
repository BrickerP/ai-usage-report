# Step 3 — Problem definition

## Look inward

The previous design assumed that more visible stages and actions would create more gameplay. Live dogfooding disproved that assumption: the fixed runner, Reveal action, Replay action, and second archive summary increased cognitive load without helping a visitor understand the history.

The product is not trying to prove that token usage is productive, efficient, or technically impressive. Its value is a memorable and truthful personal artifact. Pixel 2D is the chosen expression; the underlying problem is that accurate model histories currently feel like interchangeable filters instead of recognizably different chapters.

Further assumptions to challenge:

- “Model ability” must not become a disguised benchmark or vendor claim.
- More animation is not more interaction; a visitor action must reveal or manipulate a real date or scope.
- Cost is provider pricing, not efficiency. It remains exact context and does not select a Run Signature.
- A model with little history is not weak; its history is simply still forming.
- The owner is not the only audience. A first-time GitHub visitor must understand the premise without prior dashboard knowledge.

## Look outward

### Who feels the problem

- A curious visitor arrives from BrickerP's GitHub profile with roughly ten to thirty seconds of attention.
- They can read the lifetime total, but tool cards, model controls, charts, and repeated result panels do not quickly communicate how one model's history differs from another.
- They are willing to make one or two meaningful selections, but not complete an onboarding funnel or learn custom game controls.
- The owner wants a distinctive showpiece and a stable shareable history, not a productivity dashboard.

### Who benefits from the current form

- An analyst who already knows the controls benefits from exact tables and filters.
- A finance or procurement visitor may want billing reconciliation, ROI, or comparisons.

Those jobs remain supported by the Exact Ledger, but they are not the primary design target for the playable surface.

## Jobs to be done

| Job | Need |
| --- | --- |
| Functional | Recognize when and how a selected model appeared across the owner's real history; inspect first seen, peak, active periods, and last seen without learning a second interface |
| Emotional | Feel curiosity, recognition, and ownership because the world visibly belongs to this history rather than to a generic game template |
| Social | Share a memorable personal AI artifact that signals sustained exploration without claiming skill, output quality, productivity, or superiority |

### Top pain

Accurate model histories are available, but the presentation makes them feel like another filter and turns discovery into repeated controls and duplicated summaries.

### Expected gain

Selecting a model immediately produces a recognizably different, explorable history. The visitor can move through it, encounter its exact record, and switch to another model without an additional Reveal, Replay, or route-selection action.

## Proto-persona — pending validation

### Curious collaborator

- **Context:** follows a link from a GitHub profile on desktop or mobile.
- **Trigger:** wonders what BrickerP actually uses and whether the history has a recognizable shape.
- **Knowledge:** understands AI tool and model names; may not understand token accounting details.
- **Current workaround:** reads the Hero number, scans one chart or card, and leaves before discovering model-level history.
- **Success moment:** selects two models, sees two unmistakably different maps, explores a real date, and correctly explains one model's Run Signature and record as personal history rather than model quality.

### Explicit non-user

A manager, finance partner, procurement analyst, or benchmark seeker looking for ROI, billing audit, team ranking, productivity evidence, or proof that one model is objectively better.

## Problem statement

Visitors reviewing a long personal AI history need a fast and memorable way to recognize how individual models actually appeared across time, because accurate numbers alone make those histories difficult to distinguish and remember, while extra reveal steps make exploration feel like form completion rather than discovery.

## How might we

How might we make every selected model's recorded history immediately distinct and explorable, while ensuring that every difference is traceable to real dates and usage and that no additional action stands between selection and discovery?

## Run Signature semantic contract

- A Run Signature describes this owner's observed usage rhythm. It does not describe model intelligence, quality, speed, expertise, efficiency, or productivity.
- One model and one normalized history scope produce at most one dominant signature.
- Inputs are limited to daily recorded tokens and active dates. Exact cost is displayed but does not determine the trait.
- The signature always exposes its model, date scope, sample size, and plain-language trigger evidence.
- The same normalized history and rule version must produce the same map and signature.
- Missing, incomplete, zero-day, or insufficient samples produce a neutral map and `History still forming`.
- No cross-model strength ranking, rarity, level, score, medal, achievement, or negative trait is permitted.
- The selected range supplies the history scope; All time is the canonical default.
- Published report dates and timezone are authoritative. Unknown model arrays are unavailable, not silently imputed.

## Observable success signals

### First-time comprehension

- Within five seconds, a visitor can identify the lifetime record and the invitation to explore models.
- Without instructions, the visitor can select a tool and model without first entering a separate game mode.
- After model focus, the map and record are available immediately; there is no Reveal action.

### Distinctive exploration

- Within thirty seconds, the visitor explores at least one real date and can identify the selected model, its peak value/date, and whether the Run Signature describes history rather than capability.
- Switching to a second model visibly changes map geometry, landmarks, record, and signature when the histories differ.
- A visitor can revisit any date directly instead of replaying a fixed animation.

### Truth and determinism

- Every landmark maps to a published date and exact value.
- Identical normalized history produces identical geometry and signature.
- Changes to daily tokens or active dates can change the result; unrelated fields cannot.
- Visitors describe the result as a usage pattern, not as proof of speed, quality, skill, or effectiveness.

### Interaction and accessibility

- Mouse, touch, keyboard, and reduced-motion paths expose the same dates, values, landmarks, and scope.
- Model selection remains synchronized with the chart, URL, visible state, and one concise ARIA announcement.
- Keyboard focus remains on the triggering control; the experience does not steal page scrolling or use `role="application"`.
- Map identity is not communicated by color or motion alone, and the exact accessible table remains available.

### Simplification

- The playable history introduces no new primary button.
- Independent Enter Run, Reveal, Replay, Choose another route, and Lifetime Archive toggle paths no longer exist.
- Lifetime facts appear once in a compact save strip; model record facts appear once in the active history surface.

## Dogfood acceptance task

A visitor opens the default All-time page, selects one tool and model, explores first seen, record, and last seen, switches to a second model, and explains why the two histories differ. The task passes only if it requires no instruction, no duplicated result surface, and no invented performance claim.

**Decision requested:** approve this problem definition before the prototype specifies map geometry, signature thresholds, and the single traversal interaction.
