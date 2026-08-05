# Step 5 — Implementation Truth Source

Status: approved for implementation on `codex/run-the-archive`.

In this project, “MVP” means the complete approved vertical journey, not a reduced visual demo. A visitor must be able to focus a real model and traverse its real history end to end.

## A. Prompt Architect

### One-sentence problem

The current page looks like a game but still behaves like a dashboard plus a separate scripted Run Stage; make the focused model's exact daily history become the explorable game world itself.

### Target user

A curious GitHub visitor exploring BrickerP's personal AI usage history. A finance, productivity, or model-benchmarking user is explicitly not the target.

### Goal

Deliver one honest loop:

```text
Choose tool → view models → Focus model → world regenerates → traverse dates
→ encounter the real Record → continue to Last Seen / Archive Edge
```

### Non-goals

- No new ingestion, reconciliation, cache, cost, or publishing semantics.
- No XP, level, rank, rarity, reward, productivity, quality, or vendor capability claims.
- No Start/Reveal/Reach Record/Replay/Run Complete/Lifetime Archive workflow.
- No forced tutorial, scroll hijack, global arrow-key handler, audio, NPC, inventory, or separate Canvas game.
- No log/sqrt transform, clipping, cap, averaging, or downsampling.
- No new dependency, abstraction framework, feature flag, compatibility path, or dormant legacy state.
- No Beijing/Second Ring expansion; it is unrelated to this archive world.

### Required journey

1. Default remains All time with the existing lifetime Hero as the only Hero number.
2. Choosing a tool updates the current tool scope and exposes its real model roster.
3. Choosing a model is the existing Focus action; the chart, URL, selection text, accessible status, exact table, Run Signature, and map all update in the same commit.
4. The focused model world derives every terrain point from that model's daily rows, never from tool totals.
5. One visible native date range control moves the runner, camera, readout, minimap context, tooltip date, and `day` URL together.
6. The model Record is the largest mid-route landmark with the exact value; later history remains traversable.
7. Changing model generates another deterministic world without a separate reset/replay control.

### Acceptance criteria

- [ ] Default range and all published totals remain unchanged.
- [ ] Tool → models → Focus continues to update chart/URL/a11y state.
- [ ] Two models with different histories produce different daily terrain, landmarks, signature evidence, and visual grammar.
- [ ] Missing model attribution renders `Unavailable`, not zero.
- [ ] Same normalized rows + scope + signature rule version produce the same result.
- [ ] Signature is one of `Pulsar`, `Sprinter`, `Marathon`, `Hopper`, `Climber`, `Trailblazer`, or `History still forming`, with plain arithmetic evidence.
- [ ] Focused world keeps the current approved two-band extreme calculation and exact Record; it does not introduce log/sqrt/clip.
- [ ] `day=YYYY-MM-DD` deep links restore a valid focused date; invalid day values canonicalize away.
- [ ] One native range input supports pointer/touch and native Left/Right/Home/End behavior without global listeners.
- [ ] The world is decorative to assistive technology; fixed DOM readout and exact table remain authoritative.
- [ ] Model switch keeps focus on its triggering button and causes one concise polite announcement.
- [ ] Reduced-motion shows identical data and controls without camera/terrain tweening.
- [ ] 320/390px layouts have no horizontal page overflow and retain a 44px touch rail.
- [ ] Old Run Stage state, DOM, CSS, strings, selectors, and tests are deleted.

### Risks

| Risk | Required control |
| --- | --- |
| Model focus is only a recolored tool chart | Derivation function accepts focused model rows and owns every point/landmark |
| Missing optional model arrays become false zeroes | Coverage is checked before aggregation; unavailable is a separate point state |
| Game surface becomes inaccessible Canvas-only UI | One native range input, fixed readout, live model summary, exact DOM table |
| Hidden gesture replaces button complexity | Persistent short instruction and 44px date rail |
| Extreme geometry becomes misleading | Reuse two-band split, show break glyph and exact integer, prohibit nonlinear transforms |
| Signature implies capability or productivity | History-only inputs, fixed thresholds, evidence text, explicit semantic disclaimer |
| Old workflow survives as dead compatibility code | Delete it completely and add negative regression assertions |

## B. Scope Cop

### Keep

- Existing page order: lifetime Hero, all-time skyline, Loadout/model drilldown, Explore/ledger.
- Existing dynamic lifetime totals, cache disclosure, costs, dates, stale/loading/error behavior.
- Existing tool selection, model roster, Focused model semantics, exact table, URL preservation, and accessibility status.
- Existing approved two-band extreme-value mathematics.
- The approved Step 4 desktop/mobile visual language and one date traversal control.

### Cut

- Independent Run Stage and fixed progress route.
- Reveal, completion, replay, choose-route and archive-toggle controls.
- Fake completion ticket and results panel.
- Duplicate status copy and explanatory `Record Horizon / Ground / Record Sky` blocks.
- Tool-total terrain while a model is Focused.
- Any compatibility export or CSS alias for deleted Run Stage paths.

### Defer

- Share-card rendering for individual focused worlds.
- Sound, haptics, achievements, multi-record collections, comparison mode.
- User-configurable signature thresholds or themes.
- Server-side/dynamic SVG generation for model maps.

### Implementation seams

| File | Responsibility |
| --- | --- |
| `src/lib/runLevel.ts` | Pure model coverage, signature, evidence, points, landmarks and selected-day defaults |
| `src/lib/interaction.ts` | Canonical `day` URL parse/build; remove old Run Stage state derivation |
| `src/components/UsageCharts.tsx` | Focused model world, two-band terrain, Record/runner, date readout/range, exact table |
| `src/App.tsx` | Delete old stage; own selected day; connect tool/model/range/URL/live state |
| `src/index.css` | Delete old stage styles; add archive world, signature variants, mobile/forced-colors/reduced-motion styles |
| `tests/frontend_model_drilldown.test.mjs` | Replace old stage assertions with deterministic world/URL/a11y/deletion contracts |

No collector, script, generated data, dependency manifest, or deployment path belongs in this change unless remote CI proves an existing contract needs the smallest matching correction.

## C. Builder rules

1. Start with the pure `deriveRunLevel` seam and typed return contract.
2. Reuse existing usage helpers only where they preserve missing-coverage semantics; do not let optional arrays collapse to zero.
3. Reuse the existing two-band display calculation with focused-model daily values.
4. Connect one `selectedDay` state through URL and `UsageCharts`; do not create a second store.
5. Delete old Run Stage code before adding the new surface, so dead compatibility cannot survive.
6. Use existing dependencies and native browser controls.
7. Do not leave TODOs, placeholders, hidden fallback experiences, or unreferenced legacy selectors.
8. Do not compile locally. Push the branch, open a PR, and use remote CI as the build/test authority.

## D. Reviewer checklist

### Data honesty

- Focused points equal the selected model's published daily tokens/cost.
- Incomplete model arrays are unavailable rather than zero.
- Record date/value and tie behavior are deterministic.
- Signature evidence can be recomputed from visible scoped rows.
- Cache and tool-level reconciliation remain untouched.

### Interaction

- Tool selection clears stale model/day state.
- Focus generates the world in one model action.
- Range/model changes canonicalize day correctly.
- Mouse/touch/keyboard operate the same selected date.
- Record is not an endpoint and there is no completion control.

### Accessibility

- Exactly one date slider enters the tab order.
- No global keyboard listener or `role="application"`.
- Canvas/SVG is hidden from assistive technology.
- Visible and spoken selected/Focused/Record/unavailable states do not depend on color.
- Existing live region announces committed model/signature changes once.

### Cleanup

- No `run-stage`, `RUN COMPLETE`, reveal/replay/route progress, or Lifetime Archive toggle remains in source/CSS/tests.
- No new dependency or compatibility layer exists.
- `.omx/` is untouched and excluded from git.

### Remote completion evidence

- Branch pushed and PR created.
- Remote frontend test/build checks pass on the PR head.
- Review finds no P0/P1 issue against this truth source.
- Merge/deploy is a separate action unless explicitly requested.
