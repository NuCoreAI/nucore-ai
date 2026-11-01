# Routine folders: create, place, move, delete (PROPOSAL/DESIGN, 2026-10-31)

## Purpose

The hub (NuCore/IoX) models a routine folder as just another `Trigger`/program row
(`isFolder: true`), able to carry an `if` gating condition (no `then`/`else`) that controls
whether the routines nested under it get evaluated at all. None of this is exposed through
`unified` today:

- `create_or_update_routine` always creates new routines at the root (`parent=None` hardcoded
  in `src/unified/handlers/routine_automation.py`).
- On update, it always force-fetches and re-sends the routine's existing `parent`, by design —
  so an unrelated content edit can never silently detach a routine from its folder
  (`src/unified/routine_compiler/core.py:486-491`).
- There is no tool to create a folder, place a new routine inside one, move an existing routine
  into one, or delete one.
- `condensed_routines`/ROUTINES DATABASE never surfaces a routine's `parent` at all, so the
  model has no visibility into folder membership even to talk about it.

This doc designs all four phases (folder creation, placing a routine in a folder, moving a
routine between folders, deleting a folder) as one coherent feature. **All four phases (0-4:
prerequisite, folder creation, create-inside-a-folder, move-into-a-folder, delete-a-folder) are
implemented.** The write-key risk (`isFolder`) is confirmed against a live hub. The remaining
"Open risks" (#2-4 below) are narrower and non-blocking as designed, but still worth a look before
relying on this in production.

## Verified facts this design depends on

- `compile_trigger_source` (`src/unified/routine_compiler/core.py:475-536`) unconditionally
  compiles `then`/`else` and always emits `if`/`then`/`else`, even as `[]`. `_compile_bool_expr`
  (`core.py:380-401`) is already a pure condition-only compiler with zero dependency on
  actions — it is reused as-is for a folder's gating condition, no new helper needed.
- `_load_routines` (`src/iox/iox_wrapper.py:3053-3137`) builds `condensed_routines` from
  `GET /api/triggers` (real routines, carries `parent`) merged with `GET /api/programs` runtime
  summaries (`_RUNTIME_SUMMARY_FIELDS`, `iox_wrapper.py:3004-3007`) — `parent` is copied into
  neither branch today.
- The hard delete-confirmation gate is independent of any handler's own `confirmed` flag:
  `src/unified/runtime.py:213-239` wraps tool dispatch and forces `confirmed=False` unless the
  customer's own literal last message was exactly `"YES"`, for any tool in
  `_DELETE_CONFIRMATION_TOOLS = {"delete_plugin", "delete_registered_plugin"}` or any call to a
  tool in `_DELETE_CONFIRMATION_OPERATION_TOOLS = {"node_op", "routine_status_op", "variable_op"}`
  with `operation == "delete"`. Tested via `tests/unified/test_runtime.py:186-189`'s
  `gated_tool` parametrization.
- The two-call delete-confirm response shape (`{"confirmation_required": True, "message": ...}`
  when not yet `confirmed`) is hand-duplicated three times today, not a shared helper:
  `routine_status_ops.py`, `variable_ops.py`, `node_ops.py`.

## Write-key risk — RESOLVED (confirmed against a live hub, 2026-11-01)

The creation-side schema (`src/unified/routine_compiler/schemas/apiTriggers.ts`) uses `isFolder`
as the write-side field on `Trigger`/`NewTrigger`. The hub's own runtime-summary endpoint
(`GET /api/programs`) reports folder-ness via a *different*, already-correct, already-tested
field: `folder` (boolean) — confirmed in `tests/iox/test_routine_endpoints.py` fixtures.

At the time this was written, no code or test anywhere in this repo had ever actually created a
`Trigger` with `isFolder: true` against a real or mocked hub, so the design isolated the write
key to one named constant (`_FOLDER_FLAG_KEY`) in case the hub wanted something else.

**Verified via the Phase 1 live-hub checklist below (raw `curl` against a real hub, independent
of this codebase): `PUT /api/trigger` with `isFolder: true` round-trips correctly** — it appears
in `GET /api/triggers` with `isFolder`/`parent` intact, and in `GET /api/programs` with
`folder: true`; a child routine's `parent` round-trips to the new folder's id too. `_FOLDER_FLAG_KEY`
needs no change. (Risks #2-4 below are separate and still open.)

---

## Phase 0 (prerequisite) — expose `parent` so folders are visible at all

Without this, folders would be creatable but invisible to the model — it couldn't reference one
by id/name, or see which routines already live in which folder.

- `src/iox/iox_wrapper.py::_load_routines`: add `"parent": routine.get("parent")` to the
  trigger-sourced `condensed_routine` dict (~line 3108), and `"parent": summary.get("parent")`
  to the folder-only branch (~line 3124). The latter is defensive — current `/api/programs`
  fixtures suggest folder-only summary rows may not carry `parent` at all, which would only
  limit discovering a folder's *own* nesting, not any of the four phases below.
- `src/rag/dedupe_routines.py`: add a `parent` column to `DedupeRoutines.render_python`'s
  rendered tuple and its `PYTHON_LEGEND`. Folders already render as ordinary rows via the
  existing `folder: true` flag — once `parent` is present, the model references a folder exactly
  like it already references any routine, by `id`. No new id/name-resolution machinery needed.
- Update `tests/iox/test_routine_endpoints.py` and `tests/rag/test_dedupe_routines.py` expected
  literals accordingly.

## Phase 1 — folder creation

**Design decision: extend `create_or_update_routine` with a new `is_folder: boolean` argument,
rather than adding a separate tool.** Justification:

- A folder's `if` reuses the *entire* existing CONDITIONS grammar (schedule/status/control/var/
  x10/utility — everything `_compile_bool_expr` already supports, unchanged). A separate
  `create_folder` tool would have to duplicate that whole grammar section in its own tool
  description to be useful, creating two copies to keep in sync forever.
- All of create/update's supporting machinery — device/property/command id resolution, hub-error
  relay, create-then-wait-for-id, the `parent` force-fetch-on-update convention — is identical
  for a folder row vs. a routine row (both are rows in the same hub `Trigger` table, same
  `PUT`/`POST /api/trigger` endpoints). Reusing avoids forking ~70 lines of handler logic.

### Compiler — `src/unified/routine_compiler/core.py`

`compile_trigger_source` gains `is_folder: bool = False`. Isolate the unverified write key:

```python
_FOLDER_FLAG_KEY = "isFolder"  # UNVERIFIED against a live hub -- see this doc's risk note.
```

In folder mode, require exactly one top-level statement that is either `if <condition>: pass`
(gated folder) or bare `pass` (ungated, always-active organizational folder). Reject, with new
`TriggerCompileError`s following the existing "name the rule, prescribe the fix" convention
(e.g. `core.py:512`'s `elif` message):

- Any `else:` on the `if`: *"Folders do not support `else` -- a folder's `if` only gates whether
  the routines inside it get evaluated; it cannot run a separate action branch. Remove the
  `else:` block."*
- Any real action in the `if` body (today `if cond: pass` silently compiles to zero actions and
  nothing rejects a folder *with* real actions — this gap must close): *"Folders do not support
  actions -- a folder's `if` body must be exactly `pass`; folders can only gate evaluation of the
  routines inside them, never run actions themselves."*
- More than one top-level statement, or anything that isn't `If`/`pass`: analogous message.

Reuse `_compile_bool_expr(if_node.test)` unchanged for the condition. Output:
`out[_FOLDER_FLAG_KEY] = True`; **omit `then`/`else` entirely** (schema marks them optional)
rather than emitting `[]`.

On update (`trigger_id` given), ignore any `is_folder` argument — folder-ness changes are out of
scope for this feature; derive it from the existing routine, the same way `parent` already is.

### Handler — `src/unified/handlers/routine_automation.py::create_or_update_routine`

Read `is_folder = bool(args.get("is_folder"))`; forward it into `compile_trigger_source` only on
the create path (`routine_id` falsy). No other changes — the `_resolve_condition` loop already
operates on `compiled.get("if")`, and the `_resolve_action` loop already defends against a
missing `then`/`else` key (`compiled.get("then") or []`).

### Tool spec — `src/unified/tools/tool_routine_create_or_update.json`

Add `is_folder` (boolean, default false) to `input_schema.properties`: *"When true, creates a
FOLDER (an organizational container with an optional gating condition, no actions) instead of a
routine. See GRAMMAR's third alternative. Never combine with an existing id."*

Extend the `GRAMMAR:` paragraph with a third alternative: *"(3) ONLY when is_folder: true --
exactly one top-level `if <condition>: pass` statement (no else, no actions), or a single bare
`pass` with no `if` at all for an always-active organizational folder. A folder never has
then/else/actions; its if (when present) only gates whether the routines/folders inside it are
evaluated at all."*

### System prompt — `src/unified/prompt/system_prompt.md`

New, separate `# ROUTINE FOLDERS` section (near `# ROUTINES`) — explicitly distinct from the
existing device/node `# FOLDERS` section, which is `node_op`'s folders, a different hub
subsystem entirely (`/api/nodes` vs. `/api/trigger`/`/api/programs`/`/api/triggers`). Never let
the model conflate the two.

**Fix (found in live use, 2026-11-01)**: `# UI NAVIGATION`'s `## Link formats` had a `Routine`
line (`[routine name](/programs/{program_id})`) but said nothing about routine folders, so the
model had no instruction to link one after creating it. A routine folder is just another row in
the same programs/triggers table as a routine (`folder: True`), so the fix broadens that existing
line ("Routine or routine folder") rather than adding a second, duplicate link rule — folders
were never meant to need their own logic here, only to be covered by the one that already
exists. (The unrelated `Device, group/scene, or folder` line just above it is the device-tree
folder from `# FOLDERS`/`node_op` -- a different concept that happens to share the English word
"folder"; disambiguated in-line to keep the two from blurring together now that both appear
nearby.)

### Tests

- New `tests/unified/routine_compiler/test_folder.py`: folder-with-condition compiles to
  `{"name":..., "if":[...], "isFolder": True}` with no `then`/`else` keys; bare `pass` compiles
  similarly with `"if": []`; a folder with real actions raises `TriggerCompileError`; a folder
  with `else:` raises; `elif` still raises the existing error; `parent`/`comment`/`trigger_id`
  passthrough still works with `is_folder=True`; default `is_folder=False` is an explicit
  regression no-op.
- `tests/unified/handlers/test_routine_automation.py`: create a folder via
  `execute_tool("create_or_update_routine", {..., "is_folder": True})`, assert the created
  trigger has `isFolder: True`, no `then`/`else` keys, and that condition resolution still ran.

### Live-hub verification checklist — DONE (2026-11-01, confirmed working)

1. `PUT /api/trigger` a minimal `{"name": "AI Folder Verify Test", "isFolder": true, "if": []}` —
   confirm a 2xx response.
2. `GET /api/triggers` — does the new row appear, with `isFolder: true` and a `parent`?
3. `GET /api/programs` — does it appear there with `folder: true`?
4. Create a second routine with `parent` set to the new folder's id; `GET /api/triggers/{id}` for
   it and confirm `parent` round-trips.
5. Cross-check in the hub's own admin UI that the folder and nested routine actually show up
   nested — a check beyond the API's own self-report.
6. Create a folder with a real condition; confirm it round-trips intact.
7. `DELETE /api/trigger/{id}` both test objects; confirm they disappear from subsequent
   `GET /api/triggers`/`GET /api/programs`.
8. Record the outcome as a comment at `_FOLDER_FLAG_KEY`'s definition. If the hub wants a
   different key/mechanism, only that one line needs to change.

---

## Phase 2 (implemented) — create a routine inside an existing folder

- `tool_routine_create_or_update.json`: added `folder_id` — only honored on create (new routine,
  `id` omitted); combining `folder_id` with an existing `id` is rejected with a clear error
  (ambiguous with Phase 3's mechanism, below).
- `routine_automation.py`: on the create path, `parent = folder_id` is passed into
  `compile_trigger_source` (replacing the previously-hardcoded `None`). An invalid folder id
  surfaces through the existing hub-error relay — no new existence check was added.
- Tests (`tests/unified/handlers/test_routine_automation.py`): create with `folder_id=5` and
  ordinary code → `created_trigger["parent"] == 5`; create with no `folder_id` → unchanged
  root-creation behavior (regression); create with both `id` and `folder_id` → `{"error": ...}`.

## Phase 3 (implemented) — move an existing routine into a folder

The existing force-fetch-`parent`-on-update behavior is deliberate and must not be weakened by a
generic "pass parent" argument.

- `tool_routine_create_or_update.json`: added `move_to_folder_id` (nullable integer) — documented
  as *only* to be included when the customer explicitly asked to relocate this routine; omitting
  it always preserves the current folder, exactly as before. The root folder is **"My
  Programs", real id `1`** (confirmed live, 2026-11-01 — **not `0`**, despite `0` being silently
  tolerated/aliased to `1` by the hub on create; this codebase's own prompting/docs always say
  `1`, never `0`, to avoid relying on an undocumented alias).
- `routine_automation.py`: checks **presence**, not truthiness —
  `"move_to_folder_id" in args and args["move_to_folder_id"] is not None` — before overriding the
  force-fetched `parent`, since a falsy-but-explicit folder id (e.g. a hypothetical `0`) must
  still be distinguished from "key not supplied." (The code itself is agnostic to which id means
  root -- only the model-facing docs needed correcting; see the root-id note above.)
- Tests (`tests/unified/handlers/test_routine_automation.py`): move overrides the force-fetched
  parent; the key regression test confirms an update with no `move_to_folder_id` key at all
  leaves `parent` exactly as force-fetched (proves the footgun isn't reopened); an explicit `0`
  is honored as a real value distinct from "key absent" (not claimed to mean root -- see above).

**Bugfix (found in live use, 2026-11-01)**: the first cut of this phase still unconditionally
required `code`, so the tool's own contract ("`code` REPLACES the routine's entire if/then/else
content -- there is no partial-patch mode") made a pure move indistinguishable from a full
content rewrite. In practice the deployed assistant correctly refused to guess at reconstructing
a routine's exact schedule semantics and asked the customer whether to rewrite the logic just to
move it -- the tool was *working as specified*, but the spec was wrong: a move should never touch
logic at all.

Fix: a **move-only path**, triggered when `id` + `move_to_folder_id` are given and `code` is
omitted entirely. It bypasses `compile_trigger_source` completely -- it mutates only `parent` on
the already-fetched `existing` dict (from the same `get_routine` call the ordinary parent
force-fetch already makes) and resubmits that via `update_routine` directly, so the routine's
`if`/`then`/`else`/`name`/`comment` are guaranteed byte-for-byte unchanged, not merely
"carefully reconstructed." Critically, `code` also had to be dropped from the tool's JSON-schema
`required` list (`["name", "code"]` → `["name"]`) -- leaving it required would have meant Claude's
tool-calling always supplied *some* `code` value no matter what the handler could accept,
silently defeating the whole fix. The tool description's `code`/`move_to_folder_id` fields and
its "UPDATING AN EXISTING ROUTINE" paragraph now explicitly call out this exception.
`create_or_update_routine` with no `code` and no `move_to_folder_id` (an ordinary create/edit
missing `code`) is still rejected exactly as before.

**Correction (from the customer, 2026-11-01): the root folder's real id is `1` ("My Programs"),
not `0`.** This codebase's docs/prompts/tests had assumed `0` meant root throughout (consistent
with `0` showing up as a generic placeholder `parent` value in pre-existing test fixtures). The
hub does tolerate `0` on create by silently treating it as `1` -- but `1` is the real, canonical
id, and that's what every model-facing instruction (`folder_id`, `move_to_folder_id` on both
`create_or_update_routine` and `delete_folder`) now says explicitly, rather than relying on an
undocumented alias. The underlying code was never affected -- `parent`/`folder_id`/
`move_to_folder_id` are all passed through verbatim with no hardcoded meaning attached to any
particular number -- this was purely a model-facing documentation correction (tool JSON
descriptions, `system_prompt.md`, and one test's framing that had asserted `0` "moves to root").

## Phase 4 (implemented) — delete a folder

New dedicated tool, `delete_folder` — not an extension of `create_or_update_routine` or
`routine_status_op` — because folder deletion needs a contents-decision step layered on top of
the existing single-id confirm shape, plus a fan-out over children. Matches this codebase's
one-module-per-concern layout (`node_ops.py`, `variable_ops.py`, `routine_status_ops.py`).

- `src/unified/tools/tool_delete_folder.json` (new): `folder_id` (required), `contents_action`
  (`"delete" | "move"`, required once the folder is known to be non-empty), `move_to_folder_id`
  (required when `contents_action == "move"`), `confirmed` (default false).
- `src/unified/handlers/folder_ops.py` (new), `delete_folder(nucore_interface, args)`:
  1. Force a fresh read (`routines_changed = True` then `_refresh_routines_database()`) and
     enumerate children via `nucore_interface.all_routines.values()` where `parent == folder_id`
     (the backend's own existing store).
  2. Empty folder → the standard single-item delete-confirm shape when not yet `confirmed`; once
     confirmed, deletes the folder via the existing `routine_ops(folder_id, "delete")`.
  3. Non-empty, no `contents_action` yet → `{"confirmation_required": "choose_contents_action",
     "children": [...], "message": "..."}` describing both options, no side effects — this is
     the "ask the customer" step.
  4. Has `contents_action` (and `move_to_folder_id` when it's `"move"`, validated as required and
     distinct from `folder_id`), not yet `confirmed` → preview, no side effects.
  5. `contents_action` + `confirmed: true` → refuse (don't cascade) any child that is itself a
     folder (`isFolder`) with its own children (checked directly against `all_routines`, since
     nested-folder behavior isn't reliably verified against a live hub yet), reporting it by
     name/id; otherwise `"delete"` loops the existing single-delete call over every child then
     the folder itself, only if every child succeeded; `"move"` fetches each child's full dict via
     `get_routine`, mutates only `parent`, re-posts via `update_routine` (a server-side field
     mutation, not model-authored DSL — doesn't go through the compiler), then deletes the
     now-empty folder the same way.
  6. On success, flips `routines_changed = True` (same lighter-weight convention `variable_op`
     already uses post-mutation — the next turn's prompt build picks up the change; no second
     eager refresh call, since nothing here needs to read the result back immediately).
- `src/unified/dispatch.py`: imports `folder_ops`, registers `"delete_folder"`.
- **`src/unified/runtime.py:213`**: `"delete_folder"` added to `_DELETE_CONFIRMATION_TOOLS` —
  every successful call is a delete attempt regardless of `contents_action`, so it needs the same
  independent hard floor every other delete tool already has.
- `tests/unified/test_runtime.py:186`: `"delete_folder"` added to the `gated_tool` parametrization.
- New `tests/unified/handlers/test_folder_ops.py` (9 tests) covering: non-empty folder →
  `choose_contents_action` with no side effects; `contents_action` preview before `confirmed`;
  delete-all executes children then folder, aborting (folder left in place) if any child fails;
  move requires `move_to_folder_id`, re-parents children byte-for-byte (content untouched) then
  deletes the folder; empty folder skips straight to the plain confirm; a non-empty nested-folder
  child is refused, not cascaded; missing `folder_id` is rejected.

**Bugfix (found in live use, 2026-11-02): ROUTINES DATABASE went stale after routine
update/delete/move.** Reported symptom: a newly-created folder was assigned id 15 — the same id
a pre-existing routine ("Master Bathroom On") previously had — making it look like the old
routine had been silently overwritten. Root cause: `NuCoreInterface._refresh_routines_database`
(`src/nucore/nucore_interface.py:212-229`) only reloads from the hub when `self.routines_changed`
is already `True`; nothing refreshes proactively, and setting that flag after a mutation is
entirely the mutating handler's own responsibility. Three of the four mutating paths this feature
added or touched never set it, relying entirely on the hub's own `_1` ("programs updated")
websocket event arriving and being processed before the next turn's prompt build — with no
self-healing fallback the way `create_or_update_routine`'s CREATE path has via
`_wait_for_routine_id_by_name`. The most likely real mechanism behind the report: "Master Bathroom
On" was deleted via `routine_status_op` (which never touched the flag at all) in an earlier turn;
the local cache kept showing it at id 15 for some number of subsequent turns; the hub had already
legitimately freed and reassigned that id to the new folder.

Fix, each mirroring `variable_op`'s existing post-mutation `routines_changed = True` convention:
`routine_automation.py`'s move-only branch and its ordinary content-edit UPDATE branch now both
set it after a successful save; `routine_status_ops.py` now sets it after every successful
operation (not just `delete`); `folder_ops.py`'s per-child loop now sets it even on a partial
failure, so children that genuinely did mutate before a later sibling failed aren't left invisible
to the next refresh. Confirmed via a dedicated Explore pass that `create_or_update_routine`'s
CREATE path and `delete_folder`'s own full-success path were already correct -- only these three
gaps needed closing. Out of scope (a separate, deliberate design characteristic, not a bug): the
system prompt/ROUTINES DATABASE is built once per turn (`prompt_builder.py:128`,
`AgenticLoop.run`'s `messages` list assembled once before its round-trip loop) and never rebuilt
mid-turn, so a mutation made partway through a turn still isn't visible to the model until the
*next* turn -- consistent with every other mutating tool in this codebase.

## Open risks/questions carried forward (do not silently resolve)

1. ~~`isFolder` (write) vs. `folder` (read) field-naming.~~ **RESOLVED 2026-11-01** — confirmed
   against a live hub via the Phase 1 checklist; `isFolder` round-trips correctly, no change
   needed.
2. `/api/programs` summaries may never carry `parent` for folder-only rows — a folder's own
   nesting may be undiscoverable; non-blocking for Phases 1-4 as designed. Not yet specifically
   re-checked during the live-hub test above (that test used a routine child, not a nested
   folder) — still open.
3. Nested folders (folder-in-folder) are not reliably enumerable today — Phase 4 defensively
   refuses rather than silently mis-enumerating. Still open.
4. No bulk-delete/bulk-move endpoint exists on the hub — Phase 4 uses a loop over the existing
   single-item calls; a bulk endpoint, if one exists, would only be an optimization. Still open.
