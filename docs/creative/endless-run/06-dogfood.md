# Steps 6–7 — Dogfood and next loop

## Current status

**NOT RUN as of 2026-08-05.** This file is the evaluation plan and result ledger. The documentation change did not run the product, tests, build, lint, visual QA, or accessibility QA. Pending entries are not passes.

## Three real task scripts

### Script 1 — First visit, complete one run

1. Open the report with no query parameters on desktop.
2. Confirm the complete All time record is readable in Free Roam without starting the guide.
3. Enter Loadout and select one of the four real tools.
4. Inspect and focus a real model.
5. Reveal the exact Run Record, continue to Run Complete, then Replay.
6. Confirm the report remains usable and no value was presented as a score or reward.

Status: **PENDING**.

### Script 2 — Deep link and keyboard-only use

1. Open a valid tool/model deep link.
2. Use only Tab, Shift+Tab, arrows, Enter/Space, and Escape.
3. Confirm the guide reflects the current canonical selection rather than restarting or overwriting it.
4. Confirm visible state, chart, URL, focus, and ARIA announcements stay synchronized.
5. Replay and verify that guide-only reset does not corrupt the deep-link contract.

Status: **PENDING**.

### Script 3 — Mobile and reduced motion

1. Open at 390px with reduced motion enabled.
2. Read the real all-time overview, select a tool, focus a model, and reach Run Complete.
3. Confirm the path is vertical, labels and exact values remain readable, and no horizontal drag is required.
4. Exercise loading, empty, degraded, and error states where the test environment permits.
5. Confirm state changes remain understandable without travel animation or color alone.

Status: **PENDING**.

## Evaluation ledger

| Check | Expected evidence | Status | Finding/action |
| --- | --- | --- | --- |
| Default and direct access | All time Free Roam visible with no guide gate | PENDING | — |
| Six-state journey | Mouse and keyboard recordings or assertions | PENDING | — |
| Data truth | Record date/value and four-tool totals match published data | PENDING | — |
| State synchronization | Tool/model/chart/URL/focus/ARIA agree | PENDING | — |
| Responsive/reduced motion | Desktop and 390px visual review | PENDING | — |
| Loading/error/empty/degraded | Accessible state review | PENDING | — |
| Remote pipeline | Test, lint, and build job results | PENDING | — |

No observed issues are recorded because evaluation has not run; this is not evidence that defects are absent. P0 issues must be fixed before the MVP is called complete. Other friction belongs here with an explicit fix or defer decision.

## Cold-start instruction to verify

Expected page cue: **“Explore freely, or start a guided run through your real usage record.”** It must be inline, dismissible or ignorable, and must not take focus automatically.

## Step 7 — Re-brainstorm input

- Validated: **pending dogfood**.
- Disproved: **pending dogfood**.
- New opportunities: record only after observed user friction; do not add speculative mechanics.
- Provisional next cut: remove or shorten the first guide step that delays reaching a real tool/model/record without adding orientation value.
- Decision gate: choose exactly one next change after all three scripts have results; otherwise keep the current MVP boundary.
