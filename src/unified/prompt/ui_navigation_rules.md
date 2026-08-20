# UI NAVIGATION RULES

- Add a `UI Navigation` section at the bottom of your response whenever one or more specific
  devices, groups/scenes, folders, or routines were referenced in it.
- Only link an entity whose real id you already have -- from DEVICE DATABASE, ROUTINES DATABASE,
  or a tool result earlier in this conversation. Never invent or guess an id to build a link.
- Use Markdown links, with the entity's exact display name (as shown in DEVICE DATABASE/ROUTINES
  DATABASE) as the link text.
- If no specific entity was referenced, omit the `UI Navigation` section entirely.

## UI LINK FORMATS

- Device, group/scene, or folder: `[device name](/nodes/{node_id})` -- `node_id` is the real id
  exactly as shown in DEVICE DATABASE, used as-is.
- Routine/program: `[routine name](/programs/{program_id})` -- `program_id` must be a zero-padded
  4-digit hex string, no `0x` prefix (e.g. routine id `41` -> `0029`), converted from the decimal
  id shown in ROUTINES DATABASE.
