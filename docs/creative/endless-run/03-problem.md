# Step 3 — Problem definition

## Look inward

We are assuming that a narrative path will improve discovery. The product is not “a game”; the underlying need is to help a visitor understand a dense personal record without weakening direct analysis. Pixel styling is a possible expression of that need, not the problem itself.

## Look outward

- A first-time visitor sees totals, tools, models, dates, cost estimates, and charts but may not know what to inspect first.
- A returning visitor wants to reach a known tool or model immediately and must not be forced through onboarding.
- Keyboard and assistive-technology users benefit from an explicit sequence only if it preserves focus, labels, announcements, and Escape behavior.
- The existing dashboard already serves expert exploration; a mandatory flow would make their experience worse.

## Jobs to be done

| Job | Need |
| --- | --- |
| Functional | Understand the all-time record, isolate a tool, focus a model, and inspect the real peak without changing data meaning |
| Emotional | Feel invited into the history rather than confronted by a wall of metrics |
| Social | Share a distinctive personal record without claiming usage equals productivity or achievement |

Top pain: there is no obvious first route through the available detail. Expected gain: a visitor can complete one meaningful exploration and still retain full control.

## Proto-persona — pending validation

- **Context:** opens a shared AI usage report on desktop or mobile.
- **Trigger:** wants to understand which tools and models shaped the recorded activity.
- **Current workaround:** scans the headline, clicks cards and chart controls, and infers how views relate.
- **Success moment:** reaches a focused model and the exact real record, understands what changed, then returns to the full report without getting lost.

## Problem statement

Visitors need a clear but optional way to move from the complete AI usage history into meaningful detail because the relationship between overview, tool, model, and record is not self-evident, while returning visitors still need immediate direct access.

## How might we

How might we provide one observable exploration path that improves orientation without introducing invented progress, changing the data contract, or blocking normal browsing?

## Observable success signals

- The page opens in All time and remains useful without starting the guide.
- A visitor can complete the six-state loop with mouse or keyboard.
- Tool, model, chart, URL, visible state, and ARIA status remain synchronized.
- “Run Record” always shows a value and date derived from published usage data.
- Run Complete acknowledges navigation only; it grants no score or reward.
- Replay is understandable and does not corrupt deep-link semantics.

**Decision:** the problem frame is accepted for the current MVP scope; the proto-persona remains a hypothesis until dogfood evaluation.
