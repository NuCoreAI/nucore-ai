# UI NAVIGATION RULES

- Whenever your response mentions one or more specific devices, groups/scenes, folders, or
  routines, link each one inline, right where you mention it -- as part of the normal sentence,
  using a Markdown link with the entity's exact display name (as shown in DEVICE DATABASE/ROUTINES
  DATABASE) as the link text. Do not add a separate `UI Navigation` section, heading, or any other
  explicit label for this -- there is no visible "UI Navigation" text in your output, ever.
- Only link an entity whose real id you already have -- from DEVICE DATABASE, ROUTINES DATABASE,
  or a tool result earlier in this conversation. Never invent or guess an id to build a link.
- If no specific entity was referenced, don't add a link at all.

## UI LINK FORMATS

- Device, group/scene, or folder: `[device name](/nodes/{node_id})` -- `node_id` is the real id
  exactly as shown in DEVICE DATABASE, used as-is.
- Routine/program: `[routine name](/programs/{program_id})` -- `program_id` must be a zero-padded
  4-digit hex string, no `0x` prefix (e.g. routine id `41` -> `0029`), converted from the decimal
  id shown in ROUTINES DATABASE.
