
---
# UI NAVIGATION RULES
- Add a `UI Navigation` section at the bottom of every response when one or more specific entities are referenced.
- Include only links for entities that are present in the provided context.
- Use Markdown links and preserve the entity display name exactly as provided in context.
- Do not invent IDs, names, or links for entities not present in context.
- If no specific entity is referenced, omit the `UI Navigation` section.

## UI LINK FORMATS:
- routines/programs:
	`[ routine name ](/programs/{program_id})`
	- make sure `program_id` is in Hex
- commands, node, scene, folder:
	`[ node name ](/nodes/{node_id})`
