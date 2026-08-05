# USER PREFERENCES (PROPOSAL, 2026-08-03)

## Purpose

Give the LLM a durable, per-installation store of customer preferences it can read and write
across conversations -- not just within one. Motivated by two concrete use cases surfaced in
brainstorming:

- **Aliases**: personal shorthand vocabulary ("when I say 'mbr', I mean the Master Bedroom
  scene").
- **Events**: dated facts the customer wants tracked ("my dad's yahrtzeit is June 15 every
  year"), queryable later ("when is it", "do I have any routines for it") and optionally carrying
  a reminder intent ("notify me 2 days before").

Plus generic management of either: `list my preferences`, `remove this preference`.

This document is design only. Nothing here has been implemented -- written per an explicit
brainstorm-first request.

## Two shapes of preference, one store

Aliases and events behave very differently (static lookup table vs. dated/queryable/reminder-
bearing record), but both are modeled as one `Preference` record with a `type` discriminator
(`"alias"` | `"event"`) rather than two separate stores. This keeps `list_preferences`/
`remove_preference` generic and lets a future preference type (whatever it turns out to be) get
added without a new top-level store -- the same reasoning Plan already uses for `staged_ops`
(one list, an `op` field distinguishes scene/automation/variable entries).

## Design precedent: this session's own memory system

Worth naming explicitly, since it shaped the split above: the memory system underneath Claude
Code sessions already has this exact two-tier shape --

- A small, always-loaded index (`MEMORY.md`), analogous to **aliases**: cheap, static, relevant
  on nearly every turn, so it's always in context rather than fetched on demand.
- Individual detail files pulled in only when *relevant* to the current conversation, analogous to
  **events**: potentially numerous, not worth paying the token cost of every one on every turn.

The one place preferences improve on that precedent: general-purpose LLM memory has to lean on
fuzzy semantic-relevance matching to decide what to surface, because its "memories" have no
structure. Events have a real, computable trigger instead -- a date being near, or the customer
naming the event directly -- so "is this relevant right now" doesn't need to be fuzzy here.

## Relationship to Plan/Diagnostics: no session, no staging

Plan and Diagnostics are both stateful, single-in-flight-session features with a lock, because
they can drive real hub hardware or commit hard-to-reverse changes. Preferences are neither --
adding/removing one is cheap, instant, and trivially reversible (a bad alias or event is just
deleted). That puts preferences much closer to `variable_op`/`list_variables` in shape: plain
immediate CRUD, no session, no review/apply pipeline. This also matches Plan's own internal rule
that cheap/reversible operations (`create_folder`) commit immediately rather than being staged.

## Infer-and-suggest, but tool-gated writes

The LLM inferring a preference from conversation ("sounds like 'mbr' means Master Bedroom scene")
is free -- it's just generated text. Nothing is durable until a tool call actually fires, because
only a tool call (executed by the harness) can write to storage that survives past the current
conversation; nothing else in this codebase currently does (see below). That gives a natural,
almost-free confirm-before-write point: prompt-level guidance requires confirming with the
customer before calling the write tool for anything *inferred* rather than *explicitly requested*.
No separate propose/commit tool split is needed for this (see Open Questions).

## Architecture

### Where the backend lives

Same reasoning already established for Plan: this isn't hub-native data (no `/rest/...` endpoint
backs "preferences" the way devices/routines do), so it belongs in `src/unified/preferences/`, not
inside `IoXWrapper` -- no backwards import from `iox` into `unified`.

It's also, notably, the **first** feature in this codebase that needs to survive a process
restart. `SessionStore`, `PlanEngine`, and `IoXDiagnostics` are all in-memory only, scoped to the
current process's lifetime (`session_store.py` says so directly, and even flags itself as "not
thread-safe" with the concern deferred until it's an actual problem). Preferences can't take that
shortcut -- a yahrtzeit has to still be known next month.

### Storage: flat JSON file, not SQLite

- **Volume is tiny** -- tens to low hundreds of preferences per installation. A linear
  scan/filter in Python over that is nothing; there's no query SQLite would meaningfully speed up
  at this scale.
- **Matches the codebase's existing convention**: everything the LLM sees is already plain
  dict/list literals (DEVICE DATABASE, ROUTINES DATABASE, `dedupe_profiles`) -- a JSON file
  storing the same shape is one less mental model, and trivially hand-inspectable/editable while
  this feature is still evolving.
- **SQLite's genuine advantage** -- real transactional locking for concurrent writers -- isn't
  worth the schema/migration overhead here, and is deferred until there's actual evidence of
  concurrent-write contention, the same posture `SessionStore` already takes toward its own
  thread-safety.
- Mutations use atomic write-to-temp-then-rename, so a crash mid-write can't corrupt the file. No
  other concurrency handling planned for v1.

### Where the file lives

No default path -- the directory is explicitly configured, via `--preferences-dir` (CLI,
overrides) or runtime config's `preferences_dir` (`runtime_config.example.json`), with the actual
file being `<preferences_dir>/preferences.json`. An installation that hasn't set either simply
has preferences unavailable: `list_preferences`/`preference_op` return a clear
"not configured" error, and the standing prompt's aliases section says so rather than silently
picking a location. The server runs on the hub itself -- one process per installation -- so this
is a single directory, not storage keyed by an installation id; if this codebase is ever adapted
to serve multiple installations from one process, the store would need to be keyed at that
point -- not a v1 concern.

### Data model

```python
{
    "id": "p1",                    # stable, opaque id -- used by remove_preference
    "type": "alias",                # or "event"

    # type == "alias":
    "alias": "mbr",
    "target": "Master Bedroom Scene",

    # type == "event":
    "name": "Dad's yahrtzeit",
    "recurrence": "annual",         # "annual" (month/day) or "once" (a full date) -- see below
    "month": 6, "day": 15,           # recurrence == "annual"
    "date": "2026-11-03",           # recurrence == "once"
    "remind_days_before": 2,        # optional -- omitted means no reminder intent at all
}
```

One JSON file holding a list of such records per installation; `type` determines which of the
type-specific fields are populated.

### Surfacing to the LLM

- **Aliases**: always included in the standing system prompt -- a new `<<preference_aliases>>`
  placeholder in `system_prompt.md`, rendered as Python literals, same convention already used for
  `TIME & LOCATION`/`DEVICE DATABASE`. Small, and needed on nearly every turn to resolve shorthand
  -- a tool round-trip would just be wasted latency, the same reasoning already applied to
  time/location.
- **Events**: *not* dumped wholesale into the standing prompt. Unlike aliases, this category can
  genuinely grow (many events per household) and isn't relevant on most turns. Exposed via an
  on-demand `list_preferences(type="event")` call, the same treatment `list_variables` already
  gets. A query like "do I have any routines for my dad's yahrtzeit" is answered by the LLM
  cross-referencing the returned event's name against ROUTINES DATABASE it already has in
  context -- no new plumbing needed for that specific case.
  - Open question: should events due *soon* (e.g. within the next 7 days) get a small always-on
    ambient surface instead, similar to sunrise/sunset, so the LLM can proactively mention an
    upcoming yahrtzeit without being asked? See Open Questions.

### Tools

Shaped like `variable_op`/`list_variables` (a single-op mutation tool + a list tool), not like
Plan's session/staging tools, since preference edits are immediate and reversible:

- **`list_preferences(type?)`** -- returns every stored preference (optionally filtered by
  `type`), as structured data (id/type/fields), for the LLM to explain in plain language.
- **`preference_op(operation: "create" | "delete", type, id?, ...fields)`** -- `create` adds a new
  preference (the app assigns the id, returned in the response, mirroring `variable_op`'s
  create-returns-id pattern); `delete` requires only `id`. No `update` in v1 -- editing is
  delete-then-create (simplest possible shape; see Open Questions).

### Recurrence model (v1 scope)

Exactly two shapes: `"annual"` (month/day -- yahrtzeit, birthdays) and `"once"` (a single full
date -- a one-off appointment or reminder). No weekly/monthly/custom recurrence grammar in v1 --
this covers every example given so far, and richer recurrence is a clean, additive extension later
(new `recurrence` values), not a redesign.

### Notifications: explicitly stubbed

No real delivery channel exists anywhere in this codebase today -- `notify(recipient, content)` in
the routine DSL is scaffolding with no actual recipient/delivery concept (confirmed via grep). So
v1 stores the `remind_days_before` intent and exposes it through `list_preferences`/a "due soon"
query, but does not actually send anything. This mirrors how Plan ships most of its plan types as
stubs rather than half-building a delivery mechanism with no real channel behind it yet. Real
delivery (push/email/SMS/whatever channel NuCore's broader product actually uses) is a deliberate
follow-up, not attempted here.

## Open questions

1. **Ambient "due soon" surfacing** -- should upcoming events get a small always-on slot in the
   standing prompt (like sunrise/sunset), so the LLM can proactively mention them, or stay purely
   on-demand via `list_preferences`? Affects whether "notify me before X" can ever be honored
   in-conversation (rather than only when asked) before real delivery exists.
2. **Is delete-then-create good enough**, or does editing a preference (e.g. changing what an
   alias points to) need a first-class `update` from day one?
3. **Confirmation for LLM-inferred preferences** -- prompt-level guidance only (current proposal),
   or a `propose_preference` (unconfirmed) / `preference_op` (confirmed) split mirroring Plan's
   staging tiers? Leaning toward prompt-level guidance only, since a bad create is just deleted --
   the same reasoning that justifies Plan skipping staging for `create_folder`.
4. ~~Where the JSON file's path actually gets configured~~ -- **Resolved**: `--preferences-dir`
   (CLI) or runtime config's `preferences_dir`, CLI wins if both are set. Deliberately no default
   -- an installation that hasn't configured either has preferences unavailable, rather than
   silently writing somewhere unexpected.

## Status

Implemented. See `src/unified/preferences/`, `src/unified/handlers/preferences.py`,
`tool_list_preferences.json`/`tool_preference_op.json`, and the `# USER PREFERENCES` section of
`system_prompt.md`.
