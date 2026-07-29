# Research: usage-report model breakdown patterns

## Signals reviewed

- `ccusage daily --breakdown` keeps the daily report structure and adds model
  composition in the same table.
- OpenAI's usage reporting exposes model as a breakdown dimension rather than a
  separate product area.
- Anthropic's cost and usage reporting supports individual models or all models
  combined within the same reporting context.

## Applied conclusion

Model is a dimension of an existing source total, not a new top-level navigation
concept. For this report, the lowest-complexity pattern is to keep each source
card and append compact model rows that reconcile to the card total.

## Rejected patterns

- A separate model observatory duplicates the dashboard hierarchy.
- Model-first cards hide source ownership and disturb the current composition.
- Interactive drill-down makes basic model identity unnecessarily hidden.
