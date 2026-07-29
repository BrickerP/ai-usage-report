# Persona and job to be done

## Primary persona

The repository owner, checking personal AI coding usage across local machines and
account-level services.

## Job to be done

When I open the report or change the date range, I want to see which models
contributed to each existing tool total and how many tokens and dollars each
model represents, so I can understand the composition without learning a new
dashboard.

## Acceptance criteria

- The default page is recognizably the current report.
- Every model with non-zero usage in the selected range is visible in its tool
  card.
- Model token and cost totals reconcile to the card totals where the source
  exposes those fields.
- Comate no longer appears as an independent tool.
- Local Comate history is represented once under One API.
- No new statistics, navigation, or interaction mode is introduced.
