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
