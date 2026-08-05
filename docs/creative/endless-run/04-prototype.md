# Step 4 — Prototype: Run the Archive

Status: awaiting design approval. This document defines one journey only; it does not authorize implementation.

## Concept

> **The chart is the level. The runner is the date cursor.**

`Run the Archive` turns a focused model's real daily history into one deterministic side-scrolling data world. The visitor does not complete a fictional quest. They move through recorded dates, encounter the real record day, and continue to the model's actual last-seen edge.

This replaces the separate Run Stage. It does not add a second visualization or a second interaction system.

## One job

Make two model histories feel unmistakably different and directly explorable, while keeping every token, cost, date, tool/model scope, URL state, and accessible table exact.

## Primary audience

A curious GitHub visitor who wants to understand and remember BrickerP's personal AI history in under a minute. The owner and data-oriented visitor can continue into the exact ledger.

## One journey

```text
ALL-TIME OVERLOOK
Hero score + aggregate skyline
        │
        │ choose an existing tool
        ▼
TOOL LOADOUT
Tool scope is visible + its real model roster opens
        │
        │ choose a model; that existing action is Focus
        ▼
MODEL WORLD
Focused model history immediately regenerates the level,
Run Signature, chart scope, URL, status text and exact table
        │
        │ drag the date runner / click the route / use ← → Home End
        ▼
TRAVERSE THE ARCHIVE
First Seen → active terrain → quiet dates → RECORD → later history
        │
        ▼
LAST SEEN / ARCHIVE EDGE
No fake completion screen; choose another model to generate another world
```

There is no `Start`, `Reveal`, `Reach record`, `Replay`, `Choose another route`, or `Lifetime Archive` button. Model selection is the route change.

## Desktop wireframe

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ RUN THE ARCHIVE                                                             │
│ CLAUDE CODE / CLAUDE-OPUS-4-7   RUN SIGNATURE · MARATHON   ALL TIME         │
│                                  50 recorded days · longest run 12 days      │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  FIRST SEEN       working route         RECORD                LAST SEEN      │
│      │          ▂▃▆▂  ▃▅        ║       20.6B                    ◇           │
│  _╭─╯__  ____╭─█████─██╮____ // ║ // ____╭─██╮_____     _______╭─╯          │
│          quiet date              ║          later history                    │
│                         🟨 runner / date cursor                              │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│ JUL 20 2026   20,600,000,000 TOKENS   $exact cost   MODEL RECORD             │
│ [───────────────▂▃▂▅▆▂───────◆────────▃▂───────────────]                    │
│                 one 44px-high date slider + minimap                          │
└──────────────────────────────────────────────────────────────────────────────┘
```

The runner stays near 32% of the viewport while the world translates. This gives a side-scroller reading without horizontal page overflow or scroll hijacking.

## Mobile wireframe

```text
┌──────────────────────────────┐
│ CLAUDE CODE                  │
│ CLAUDE-OPUS-4-7              │
│ MARATHON · 50 recorded days  │
├──────────────────────────────┤
│       ▂▅       RECORD        │
│  ___╭███╮__ // █ // ___◇     │  220–240px stable viewport
│          🟨                  │
├──────────────────────────────┤
│ JUL 20 2026                  │
│ 20,600,000,000 tokens        │
│ $exact cost · MODEL RECORD   │
│ [──────────◆──────────────]  │  44px minimum touch rail
│ Swipe the rail or use arrows │
└──────────────────────────────┘
```

The page keeps native vertical scrolling. Only the focused date rail moves the route, with `touch-action: pan-y`. Secondary scenery density may reduce on mobile; Record, First Seen, Last Seen, exact values, and the same date range may not disappear.

## State contract

| State | Visible result | Committed data state |
| --- | --- | --- |
| All-time overlook | Existing lifetime Hero and aggregate skyline | `range=all`, no tool/model |
| Tool loaded | Tool scope, selected state, real model roster | `tool` is set; prior model/day cleared |
| Model focused | A new deterministic world and signature | `tool`, `model`, scope and a valid `day` |
| Date traversal | Runner, camera, readout and minimap move together | `day` updates with `replaceState` |
| Record date | Persistent gold tower plus exact `MODEL RECORD` stamp | The same real date/value; no new game state |
| Last seen / archive edge | Last active date and latest published edge remain distinct | No completion, reward, or invented progress |

Focus remains the meaningful committed action. Hover may preview a model label, but it does not regenerate the world or write the URL. Choosing the model performs Focus in one action; there is no second confirmation button.

## The world is generated from history

All focused terrain must come from the selected model's daily series. The current tool-total terrain cannot be reused with only a model Record marker.

| Recorded fact | Spatial expression | Meaning that must remain visible |
| --- | --- | --- |
| Calendar date | Fixed horizontal distance | Missing dates are not compressed away |
| Daily model tokens | Terrain/building height | Exact integer remains in the readout and table |
| Active date | Solid lit structure/platform | `tokens > 0` |
| Published zero | Dark ground/gap | A real zero, not missing attribution |
| Missing model coverage | Hatched fog/broken survey line | `Unavailable`, never silently converted to zero |
| First active date | Entry marker | `FIRST SEEN` |
| Earliest maximum on ties | Gold Record Tower | `MODEL RECORD`, exact date and exact tokens |
| Last active date | Signal beacon | `LAST SEEN` |
| Latest published date | Restrained survey post | `ARCHIVE EDGE`; use `NOW` only when it is truly current |

### Extreme-value geometry

The approved two-band skyline logic remains the visual scale. Its input changes from tool totals to the focused model series; its statistics do not change.

- Ordinary dates occupy the lower, linear ground band.
- Every value above the calculated split continues into the upper, linear Record band.
- A small `//` break in crossing structures exposes the split without restoring the old `Record Horizon`, `Ground / tokens per day`, or `Record Sky` explanation blocks.
- The maximum receives gold emphasis and the full integer. If more high days appear later, each crossing date receives its own upper segment; only the maximum is `RECORD`.
- No log axis, square-root transform, clipping, cap, averaging, or downsampling is allowed.
- The exact daily table remains the numeric authority.

The world is therefore expressive but not a fake linear chart. The split glyph and exact readout disclose the display break without making visitors learn internal chart terminology.

## Run Signature v1

`Run Signature` describes this owner's recorded rhythm with the selected model. It is not a vendor capability statement and never evaluates intelligence, quality, efficiency, productivity, or skill.

Input is limited to the scoped model's normalized `{date, tokens}` history. Cost is displayed exactly but does not determine the signature. Same normalized history + scope + rule version always returns the same signature, evidence, and map grammar.

Return `HISTORY STILL FORMING` with neutral geometry when coverage is incomplete, tokens are zero, there are fewer than 6 active dates, or first-to-last activity spans fewer than 7 calendar days.

For eligible history, compute active-date count, inclusive calendar span, density, contiguous active runs, longest run, top-two-day share, early/late calendar-day mean, active-day median, and pulse groups. Apply the first matching rule:

| Signature | Transparent v1 rule | Map/movement expression |
| --- | --- | --- |
| Pulsar | At least 3 separated pulse groups and pulse dates hold at least 50% of tokens | Districts illuminate in separated waves; no flashing dependency |
| Sprinter | Top 2 dates hold at least 65% of tokens | Sharp peak blocks and a short after-image when moving |
| Marathon | Density at least 70% and longest run is at least 7 days and half the span | Long connected bridge/rail and sustained stride |
| Hopper | At least 3 active runs and density at most 40% | Separated islands; runner steps across quiet ground |
| Climber | Late-half mean is at least 1.5× early-half mean with at least 3 late active dates | Ascending terraces toward the archive edge |
| Trailblazer | No rule above dominates | Mixed terrain and a restrained route trace |

A pulse date is `tokens >= 2 × active-day median`; pulse groups are separated by at least one non-pulse calendar date. The UI exposes one short evidence line, for example `8 of 10 dates active · longest run 7 days`. It never exposes a score, rarity, rank, level, reward, positive/negative judgment, or model comparison.

Signature styling never carries data alone. Its name and evidence remain readable in text; reduced-motion and forced-colors modes keep the same meaning.

## One traversal control

The visual runner and a native range input are two views of the same date index.

- Pointer: click the route to choose the nearest date, or drag the rail.
- Touch: drag only the 44px-high rail; vertical page scroll remains native.
- Keyboard: focus the range input, then use native `Left`, `Right`, `Home`, and `End`.
- The persistent hint is `Drag the date runner or use arrow keys`; it may become visually quiet after first interaction but remains available to assistive technology.
- Day movement changes the date, runner, camera, exact readout, minimap, tooltip context, and URL together.
- Reaching the Record date produces one short gold stamp/impact. The route stays open to later dates.
- Reduced motion swaps every traveling/tween animation for an immediate state change.

## URL and focus contract

- Preserve `tool`, `model`, `range`, `from`, `to`, unrelated query parameters, and the existing hash.
- Add `day=YYYY-MM-DD` only with a valid focused tool/model and a published row inside the resolved range.
- Invalid or out-of-range `day` is removed, never clamped to a different date.
- A newly focused model starts on its first active date. A deep link with a valid `day` restores that date.
- Date traversal uses `replaceState`, so the shared URL is exact without filling browser Back history with every step.
- Tool change clears model/day. Range change retains `day` only if still valid; otherwise it starts at the focused model's first active date.
- Model switches do not steal focus or programmatically scroll the page. Focus stays on the model button that triggered the world change.

## Accessibility contract

- No `role="application"`; tool and model controls stay native buttons with visible selected/Focused text.
- The world is a labelled region. Decorative Canvas/SVG terrain is `aria-hidden="true"`.
- The date explorer is one native range input, not hundreds of focusable chart marks.
- `aria-valuetext` reads `date, exact tokens, exact cost, active/quiet/unavailable, landmark`.
- The existing single atomic polite live region announces only committed model/signature changes, for example: `Focused on claude-opus-4-7. Run Signature: Marathon. 50 active dates, all time.` It does not chatter on every arrow press.
- The fixed DOM readout and captioned exact daily table remain usable if the visual world fails to render.
- Zero, unavailable, ordinary, and Record states differ by text and shape/texture, not color alone.
- Acceptance includes 200% zoom, forced colors, visible keyboard focus, reduced motion, and 320/390px widths without horizontal page overflow.

## Visual brief

### Subject

A personal AI history rendered as a collectible night-survey side-scroller: adult, exact, and unmistakably interactive.

### Palette

| Role | Color |
| --- | --- |
| Night archive | `#07111B` |
| Rail slate | `#1B2B38` |
| Moon paper | `#E8EEF2` |
| Record gold | `#F4C45A` |
| Signal cyan | `#62D6CA` |
| Activity coral | `#E26042` |

### Type roles

- Existing clean sans: explanation and navigation.
- Restrained pixel/monospace uppercase: HUD, landmark names, signature.
- Tabular monospace numerals: dates, tokens, cost.
- Pixel type never carries paragraphs or small-print disclosures.

### Signature element

The **date runner locked to the moving history landscape**, with the Record Tower as a spectacular mid-route event. This is the one identifying visual system; no Beijing world-building, commercial-game imitation, generic neon cyberpunk, fake collectibles, or separate badge wall is added.

Thin 1–2px survey lines, restrained industrial-print texture, wide negative space, and a small amount of gold/cyan replace the current accumulation of thick panels and repeated game labels.

## What is removed

Implementation must delete, not retain behind compatibility paths:

- independent Run Stage and fixed 0/33/66/100 route;
- `Reveal run`, `Reach record`, `Replay run`, `Choose another route`;
- `RUN COMPLETE` and fake completion state;
- Lifetime Archive open/close control;
- the separate decorative runner route;
- obsolete state, event handlers, CSS, copy, selectors, and tests supporting those paths.

Lifetime peak, active days, and tools used remain as one compact, always-visible Save Strip. Detailed cost, cache split, date controls, and the exact ledger stay in Explore.

## Self-questioning

**Is this still an ECharts skin?**

No. A single date control moves the visitor through a spatial grammar with real calendar distance, active structures, quiet ground, unavailable fog, persistent landmarks, camera movement, exact readout, URL restoration, and model-derived map rules. If those contracts are absent, the implementation is rejected as a chart skin.

**Does the game layer exaggerate the model or the owner?**

No. It makes the record memorable, but every claim is a date/token/cost fact or an explicitly scoped rhythm classification. There is no productivity or model-quality claim.

**Does the extreme become less impressive?**

No. The approved two-band linear split stays; the peak becomes the largest physical landmark and carries its exact full value. The rejected log/sqrt/clip approaches are not used.

**Have we replaced button complexity with hidden gestures?**

No. The date rail is permanently visible, at least 44px high, labelled with a short instruction, and uses native slider keyboard behavior. It is the only new traversal control.

**What happens when histories grow or gain more records?**

The map is regenerated from the same dates. Every threshold-crossing day gains an upper structure, the earliest maximum on ties is the sole Record Tower, the minimap preserves full context, and the viewport follows the date cursor. No badge wall grows indefinitely.

## Approval gate

Before implementation, the prototype must pass these checks on paper:

- In five seconds, a visitor can identify the model, current date, encoded metric, and meaning of Record.
- Selecting two models produces materially different geometry, landmarks, signature evidence, and movement grammar from their real histories.
- Record is spectacular, exact, and not the endpoint.
- Mouse, touch, keyboard, reduced-motion, and screen-reader journeys reach the same dates and values.
- No extra primary button, forced tutorial, scroll hijack, fake reward, or invented metric remains.
- When Canvas/SVG is absent, the selected model, fixed readout, controls, and exact table still provide the full information.

Approval of this document moves the project to Step 5: implementation scope. It does not yet approve code changes.
