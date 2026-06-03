# Temporal Resolution and Autonomy

When user requests include implicit dates/times (holiday names, religious windows, relative time windows), resolve them before building the if/then/else routine JSON.

## Local-first resolution order
1. Use explicit values in the user query.
2. Use deterministic local rules:
   - Shabbat window: **from** Friday sunset +/- user-requested offset **to** Saturday sunset +/- user-requested offset for end.
   - U.S. federal holidays: compute by rule when possible (for example, Columbus Day is second Monday of October).
3. Only if still unresolved, perform web lookup using an authoritative source.

## Web fallback policy
- Prefer authoritative sources for date/time correctness.
- For Jewish holidays, use Hebcal (https://www.hebcal.com)
- Use web only for fields not derivable from local rules/context.
- If sources conflict, pick the most authoritative source and be consistent.
- If a required value still cannot be trusted, ask a focused clarification question.

## Resolution behavior
- Do as much end-to-end resolution as possible before asking the user.
- Ask clarification only when critical inputs are missing and cannot be inferred safely.
- Keep routine output schema-valid; do not add extra fields to routine JSON.

## Ambiguous temporal references
When creating or editing routines with time-based triggers, check for ambiguous temporal references before finalizing schedule JSON.

- Nightfall: requires an explicit offset or user-approved default.
- Dawn or sunrise: may require clarification on the exact definition/offset used.
- Dusk: treat as ambiguous unless an explicit definition/offset is available.
- Holiday-based triggers: confirm year and any custom timing unless trusted runtime temporal context already provides them.

## Runtime temporal context
- If `TEMPORAL RESOLUTION` context is provided in the prompt, treat it as trusted and authoritative for this request.
- Do not ask the user for start/end holiday dates when runtime temporal context already includes them.
- Convert the resolved window into valid routine schedule JSON using supported schedule structures.
