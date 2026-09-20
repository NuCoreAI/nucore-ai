# Plugin concepts & lifecycle — the synthesis doc

> Reference doc, not a proposal. This is the entry point for the three other documents in this
> folder — read this first, then follow the links out to whichever one has the detail you need.
> It also tags every discrepancy found while cross-referencing them (§4) — treat those as open
> questions to verify in code, not settled facts.

## 0. The documents in this folder, and when to reach for each

| Doc | Covers | Written from |
|---|---|---|
| [`nucore_domain_model.md`](nucore_domain_model.md) | How nucore-ai **consumes** the hub's device model: `Node`/`NodeDef`/`Property`/`Command`/`Editor`/`LinkDef`/`Profile`/`Family`/`Instance`, and the ordinary command-dispatch path. | Reading `src/nucore/*.py` |
| [`plugin_model.md`](plugin_model.md) | How a **plugin's own backend** authors its device model and sends it to PG3/IoX at runtime (Dynamic Profiles JSON API). | Reconstructing developer.isy.io's Dynamic Profiles doc |
| [`runtime_plugin.md`](runtime_plugin.md) | How nucore-ai **manages plugins as a marketplace product** (install/buy/list/configure/start/stop) and **calls into a plugin's own LLM-tool surface** (`get_plugin_prompt`/`get_plugin_tools`/`handle_plugin_llm_result`). | Reading `src/nucore/nucore_interface.py`, `src/iox/iox_wrapper.py`, `src/unified/handlers/plugin_management.py` |

These are three different vantage points on the same overall system, and they don't fully agree
with each other where they overlap — §4 catalogs the specific gaps. None of the three claims to
be complete on its own.

## 1. Core concepts — a unified glossary

Every concept below is defined once here; follow the doc link for the full field-level detail.
Where the plugin-authoring JSON (`plugin_model.md`) and nucore-ai's internal consumption model
(`nucore_domain_model.md`) use different field sets for what should be "the same" object, both
are shown side by side and the mismatch is tagged **[G-n]**, cross-referenced into §4.

### Node

A live device/entity instance on the hub. Always on the *consumption* side — a plugin doesn't
submit "nodes" via Dynamic Profiles, it submits `nodedefs` (types); actual `Node` instances are
created separately (e.g. via the plugin's `DISCOVER` command, per the example in
`plugin_model.md` §3). See [`nucore_domain_model.md` §2](nucore_domain_model.md#2-node-and-nodebase).

### NodeDef — the type a Node is an instance of

| Field | Spec (`plugin_model.md`, what a plugin submits) | Internal (`nucore_domain_model.md`, what nucore-ai models) |
|---|---|---|
| `id` | ✅ | ✅ |
| `name` | ✅ | ✅ — populated from `nls` (legacy) or `name` (current) **[G-1]** |
| `desc` | ✅ (optional) | ✅ — added for parity **[G-1]** |
| `properties` | ✅ | ✅ |
| `cmds.sends` / `cmds.accepts` | ✅ | ✅ (`cmds: NodeCommands`) |
| `links.ctl` / `links.rsp` | ✅ | ✅ (`links: NodeLinks`) |
| `icon` | ✅ (optional) | not a separate field — folded into `customicon` **[G-1]** |
| `customicon` | ✅ (optional, "future use") | ✅ — populated from `icon` (legacy) or `customicon` (current) **[G-1]** |
| `nls` | not present in spec | not a separate field — folded into `name` **[G-1]** |

Full detail: [`plugin_model.md` §3](plugin_model.md#nodedef-object),
[`nucore_domain_model.md` §3](nucore_domain_model.md#3-nodedef--the-type-a-node-is-an-instance-of).

### Property — live value vs. schema

Spec `Property` (submitted by a plugin as *schema*: `id, name, editor, desc?, hide?`) maps to
nucore-ai's `NodeProperty` (schema: `id, name, hide, editor, desc`) — full parity now
(`desc` added, unused by any code today, stored for parity only — **[G-1]**). Separately, nucore-ai also
has a `Property` class of its own — but that's the *live value* holder (`id, value, formatted,
uom, prec, ...`), not the schema; don't confuse the two same-named things. See
[`nucore_domain_model.md` §7](nucore_domain_model.md#7-properties--live-value-vs-schema).

### Command / Cmd

| Field | Spec `Cmd` | Internal `Command` |
|---|---|---|
| `id` | ✅ | ✅ |
| `name` | ✅ | ✅ (optional) |
| `native` | ✅ (typed as `string`, e.g. `"false"`) | ✅ — added for parity, unused **[G-2]** |
| `desc` | ✅ (optional) | ✅ — added for parity, unused **[G-2]** |
| `format` | ✅ (optional) | ✅ (optional) |
| `parameters` | ✅ (optional, `Parameter[]`) | ✅ (`CommandParameter[]`) |

`sends` vs. `accepts` is the one piece of terminology that's **identical** across both docs —
commands sent *to* the device vs. commands the device itself emits. Full detail:
[`plugin_model.md` §3](plugin_model.md#cmd-object),
[`nucore_domain_model.md` §4](nucore_domain_model.md#4-command-cmdpy).

### Parameter

Spec `Parameter`: `id, editor, optional?, init?, desc?, name?`. Internal `CommandParameter`
(`cmd.py:7-24`) has `id, editor, name, init, optional` — matches the spec except `desc`, which
is not present (confirmed by direct read, not just the original research pass). Unlike
`Command.desc`/`native` (G-2) and `NodeProperty.desc` (G-1),
`CommandParameter.desc` was **not** added in this pass and remains open if parity is wanted
there too. `init` means the same thing on both sides: a
property id the parameter's UI default should be seeded from.

### Editor / Range — the legal value space

| Field | Spec `Editor` | Internal `Editor` (`editor.py`) |
|---|---|---|
| `id` | ✅ | ✅ |
| `ranges` | ✅ | ✅ |
| `is_reference` | not present in spec | ✅ field exists, but **dead** — always `False` **[G-3]** |

| Field | Spec `Range` (min/max shape) | Internal `EditorMinMaxRange` |
|---|---|---|
| `min`/`max`/`prec`/`step`/`uom`/`names`/`desc` | ✅ | ✅ (all except `desc`, per `nucore_domain_model.md`) |
| `id` | **not present** in the spec's Range table | present on both range types, but always a copy of the parent `Editor.id` — dead weight from a removed dedup feature **[G-4]** |

Full detail: [`plugin_model.md` §3](plugin_model.md#editor-object),
[`nucore_domain_model.md` §9](nucore_domain_model.md#9-editors--the-legal-value-space-editorpy-numeric_enumpy).

### LinkDef

The cleanest alignment of any object between the two docs: `id, name, protocol, cmd(bool),
format, parameters` on both sides, and the "native link" matching rule (same `protocol` string
on a controller-role and responder-role linkdef ⇒ linkable) matches nucore-ai's own
`ctl`/`rsp` split exactly. See [`plugin_model.md` §3](plugin_model.md#linkdef-object),
[`nucore_domain_model.md` §5](nucore_domain_model.md#5-linkdef-linkdefpy).

### Profile, Family, Instance

The spec's `Profile` object is **flat**: `{nodedefs[], editors[], linkdefs[]}` — nothing about
family or instance anywhere in it. Nucore-ai's internal `Profile` catalog is **nested**:
`Family → Instance → {editors[], linkdefs[], nodedefs[]}`, and every live `Node` resolves its
`NodeDef` via the composite key `"{node_def_id}.{family}.{instance}"`. A plugin's submission
has no way to specify family/instance itself — both are assigned by the platform, not the
plugin. Every plugin-originated device is unconditionally `family = "10"`
(`DEVICE_FAMILY_PLUGIN`, a fixed constant confirmed at `src/iox/iox_definitions.py:78,86`), and
`instance` is the plugin's install-time **slot** number — see **[G-5]** in §4 for the full
explanation (multiple instances of the same plugin are possible, each its own slot). See
[`nucore_domain_model.md` §12](nucore_domain_model.md#12-profile-family-instance) for the full
family-code table.

### AI-capable plugins

Everything above (NodeDef/Property/Command/Editor/LinkDef) is the **device-exposing** side of a
plugin — none of it is required. A plugin can *also* declare a `prompt` and `tools` in its
deployment manifest, separately from any NodeDefs it submits. Doing so marks it **AI-capable**:
it's offering LLM-callable capability beyond simple device commands, discovered by nucore-ai at
runtime via `get_plugin_capabilities` (not something visible in the Dynamic Profiles
`Profile`/`NodeDef` JSON itself — `plugin_model.md` doesn't cover this manifest concern at all,
it's a separate declaration). This is real and working today, not a proposal — see §3 Stage 5b
for the runtime mechanics and worked example.

## 2. How to develop a plugin using Dynamic Profiles

This is the practical path for a plugin author (condensed from
[`plugin_model.md`](plugin_model.md) — see it for full field tables and JSON/Python examples).

**Requirements**: IoX 6.0.6+, PG3x 3.4.5+, `udi_interface` 3.4.5+. A plugin uses *either*
static profile files *or* Dynamic Profiles, never both — sending a dynamic profile makes PG3/IoX
ignore any static profile files from then on.

**1. Design the device model** — decide, for each node type the plugin exposes:
   - Its `NodeDef` (`id`, `name`, `icon`) — the type/template.
   - Its `properties[]` — each with an `id` (`ST`, `GV0`, ...), a `name`, and an `editor`
     reference (not an inline value space — editors are defined once and referenced by id).
   - Its `cmds.accepts[]` / `cmds.sends[]` — commands the device can receive / commands it emits,
     each optionally carrying `parameters[]` (also referencing editors).
   - Its `links.ctl[]` / `links.rsp[]` if the device participates in native INSTEON/Z-Wave-style
     scene links.

**2. Define the `editors[]`** the nodedefs above reference — each editor is a named,
reusable value-space: a `min`/`max`/`step`/`uom` numeric range, or a `subset` + `names` enum
map. Define once, reference by `id` from as many properties/parameters as needed.

**3. Send the profile**:
   - New plugin, no existing static files: build the full `{nodedefs, editors, linkdefs}`
     object and send it via `polyglot.updateJsonProfile(profile, {"waitResponse": True})`
     before `polyglot.ready()`.
   - Incremental updates later (e.g. a newly discovered device needs a new nodedef): send only
     the changed items — `updateJsonProfile` add/replaces by matching `id`, no need to resend
     the whole profile.

**4. Use `format` strings** where a command or link parameter should render nicely in the
   program editor / scene UI, using the `${c}`/`${v}`/`${vo}`/`${uom}`/`${op}` template
   variables (see [`plugin_model.md` §4](plugin_model.md#4-formatting-in-programs-and-scenes)
   for the exact grammar and worked examples).

**5. Migrating an existing static-profile plugin**: call `getJsonProfile({'waitResponse':
   True})` once (after a startup delay, so PG3 has finished uploading the static files first) to
   dump the equivalent JSON, adopt it as the plugin's `profile` variable, send it via
   `updateJsonProfile`, then **delete the plugin's static profile folder** — leaving it in place
   causes PG3x to re-upload it on every startup, racing the JSON update. Full step-by-step in
   [`plugin_model.md` §5](plugin_model.md#5-migration-from-static-profiles-to-dynamic-profiles).

None of this — steps 1 through 5 — happens inside nucore-ai's own codebase. It all happens in
the plugin's own backend process, talking to PG3/IoX directly. nucore-ai only ever sees the
*result* (§3 below).

## 3. What happens at runtime — the full lifecycle

Stages 0-2 are outside nucore-ai; stages 3-6 are inside it. See **[G-6]** in §4 — the true
lifecycle (Stage 0 + Stage 1) is simpler than, and distinct from, what these docs individually
cover, and two of its steps aren't documented anywhere in this folder yet.

**Stage 0 — Developer lifecycle (mostly undocumented here).** Before a plugin is installable by
anyone: (1) **develop** — write the plugin's backend and its Dynamic Profile
(`plugin_model.md`); (2) **test locally** — publish to a local plugin store running on the
developer's own machine; (3) **publish to the production store** — the plugin becomes
installable by customers. Steps 2 and 3 each need their own document; neither exists in this
folder today.

**Stage 1 — Customer-side lifecycle (marketplace).** Once published, a customer manages an
installed plugin through six actions: **install** (may require payment), **configure**,
**start**, **stop**, **restart**, **delete**. Inside nucore-ai, `install_plugin`/`buy_plugin`/
`delete_plugin` never complete their action directly — they hand back a URL for a human to
finish; `configure_plugin` and `plugin_ops` (start/stop/restart) act directly
(`runtime_plugin.md` §2, §4). Once installed, the plugin's `profileNum` becomes its `plugin_id`
for every later call (`runtime_plugin.md` §2).

**Stage 2 — Plugin defines its device model.** The plugin's own backend process starts, and
(per §2 above) sends its `NodeDef`/`Editor`/`LinkDef` set to PG3/IoX via `updateJsonProfile` (or
ships static profile files — same eventual effect from IoX's point of view). This is entirely
between the plugin and PG3/IoX; nucore-ai is not involved and does not see this JSON directly.

**Stage 3 — IoX ingests the profile into its catalog.** PG3/IoX folds the plugin's submitted
`{nodedefs, editors, linkdefs}` into the hub's overall profile catalog under `family = "10"`
(`DEVICE_FAMILY_PLUGIN` — a fixed constant, confirmed at `src/iox/iox_definitions.py:78,86`;
every plugin-originated device is family `"10"`, unconditionally) and the **instance slot** the
platform assigned this plugin at install time — usually one slot per plugin, but the platform
supports installing multiple instances of the same plugin side by side (rare), each getting its
own `instance` value. This assignment is done by the NuCore/IoX platform itself, not by the
plugin and not by nucore-ai's Python code — see **[G-5]** in §4 for the full explanation.

**Stage 4 — nucore-ai loads the catalog and resolves live nodes.** On (re)connect, nucore-ai
runs the eager, one-shot load sequence from `nucore_domain_model.md` §13:
`__load_profile__` (`GET /rest/profiles`) → `__load_nodes__` (`GET /rest/nodes` XML) →
`__load_groups_links__` (`GET /api/groups/links`) → `_load_devices`, which calls
`Profile.map_nodes()`. For a plugin-originated device, this resolves its `Node.node_def` via
`"{node_def_id}.10.{instance}"` against the catalog built in Stage 3 — **the same generic
family-indexed machinery used for INSTEON/Z-Wave/Zigbee/Matter devices**
(`nucore_domain_model.md` §14) — a plugin device is not a special case in this code path.

**Stage 5a — Ordinary device commands (the common path).** Once resolved, a plugin-originated
`Node` behaves exactly like a native-protocol node for everyday control: the LLM picks a
property/command by name, `resolve_property_id`/`resolve_command_id` do a strict exact-match
lookup against that node's own `node_def` (`nucore_domain_model.md` §4), `resolve_value`
converts the customer's value against the matched `Editor` (`nucore_domain_model.md` §10), and
the result is sent to the **standard** `.../rest/nodes/{device_id}/cmd/{command_id}` endpoint —
the same endpoint used for INSTEON/Z-Wave devices. Nothing about this path is plugin-specific.

**Stage 5b — AI-capable plugin path (a separate, parallel mechanism, proven at runtime today).**
Independently of 5a, a plugin can declare itself **AI-capable** (see the "AI-capable plugins"
entry in §1) and offer LLM-callable capability beyond its ordinary device commands. This goes
through a **completely different** surface that does not touch `Node`/`NodeDef`/`Command` at
all, and the trigger, tool sequence, and result handling are all documented directly in
`src/unified/prompt/system_prompt.md`'s "PLUGIN WORKFLOW" section:

- **Trigger** (`system_prompt.md:169`, quoted verbatim): *"When no existing tool can satisfy
  what the customer's asking for, check whether a plugin can"* — broader than "no device," and
  this is **prompt-level LLM guidance**, not Python branching. Confirmed by reading
  `dispatch.py`/`runtime.py`/`loop.py`: there is no device→plugin fallback logic in code
  anywhere — the LLM decides this itself, via the agentic tool-calling loop, because the system
  prompt tells it to.
- **Tool sequence**: `list_installed_plugins` → `get_plugin_capabilities(plugin_id)` (fetches
  the plugin's own `get_plugin_prompt`/`get_plugin_tools` over `/api/plugin/{id}/prompt`+`/tools`,
  tool names uniquified as `{plugin_id}_{tool_name}`) → `call_plugin(plugin_id, tool_name, args)`
  (strips the prefix, POSTs to `/api/plugin/{plugin_id}/request`, 60s timeout)
  (`runtime_plugin.md` §5).
- **The fetched `prompt` is "usage guidance," not a literal system-prompt swap**
  (`system_prompt.md:194`, its own term). `get_plugin_capabilities`
  (`plugin_management.py:214-233`) returns it as an ordinary tool-result string; nothing in code
  re-routes it into a system-prompt slot for a sub-call. The LLM reads it in context and follows
  it purely at its own discretion — enforced by instruction-following, not by code.
- **Installing a plugin the customer needs is consent-gated, not proactive**: if no suitable
  plugin is installed yet, `install_plugin`/`buy_plugin` are available, but only after the
  customer has explicitly agreed — `system_prompt.md:181` says "never speculatively." There is
  no LLM-facing `configure` action for this flow — `configure_plugin` exists only as a backend
  method (`NuCoreInterface`), with no `tool_plugin_configure.json` schema.
- **Result**: `system_prompt.md:196`, quoted verbatim — *"using the result to answer the
  customer or to build a scene/automation from"* (nucore-ai's own term is "scene/automation,"
  not "program/routine").

**Worked example, already running in production**: a Hebrew-calendar ("hebcal") plugin — the
example is baked directly into the LLM's own instructions, `system_prompt.md:170-172`: *"A
plugin may already compute or resolve exactly the information you'd otherwise ask for (e.g. a
Hebrew-calendar plugin deriving a Hebrew yahrtzeit date from a Gregorian one, instead of asking
the customer whether they happen to know the Hebrew date themselves)."* The customer asks
something a device can't answer, the LLM finds no existing tool covers it, checks installed
plugins, finds the hebcal plugin is AI-capable, fetches its prompt/tools, calls it, and uses the
result to answer — before asking the customer any clarifying question, per `system_prompt.md:170`.

Today this path is still stateless and single-shot at the protocol level: a single plugin tool
call cannot itself ask a follow-up question, call another tool, or persist state across calls
within that one call. See Stage 6 for the parked proposal to extend this.

**Stage 6 — Where this is headed (not built).** Two parked proposals extend Stage 5b:
`design/future-consideration/agentic-plugins-future-consideration.md` (multi-turn/stateful
plugin calls) and `design/future-consideration/plan-design-future-consideration.md:83-150` (an
unbuilt `get_tool(s)()`/`get_prompt()`/`handle_llm_result()` contract for the plugin's own
backend to implement, plus the trust-model reasoning for keeping third-party plugins sandboxed
to their own declared API). See `runtime_plugin.md` §8.

## 4. Discrepancies & gap analysis

Each item is tagged with a status. **Confirmed** = both source docs directly disagree or one
clearly omits something the other states plainly. **Needs verification** = the two research
passes behind these docs didn't fully agree with each other, or the source material didn't
cover it — a developer should re-check the actual source file before relying on it.
**Resolved** = originally flagged as open, since settled by reading source directly or from
team knowledge — kept here (rather than deleted) as a record of what the open question was and
how it was answered.

- **[G-1] Resolved — `nls`/`icon` were never a second concept, they were the legacy names for
  `name`/`customicon`; `NodeDef` now has one field for each, not two.** The first pass at this
  fix (superseded) treated `name`/`desc`/`customicon` as brand-new additive fields and left
  `nls`/`icon` in place alongside them. Per the team, that was wrong: `nls` and `name` are the
  same concept under two different raw-key names (`nls` from the legacy/native profile format,
  `name` from the current Dynamic Profiles format), and likewise `icon` and `customicon` — never
  both populated on the same real profile. Corrected: `NodeDef` (`nodedef.py:96-102`) no longer
  has `nls` or `icon` fields at all — confirmed unused anywhere else in the codebase before
  removing them (`nls` only appeared in a dead, already-commented-out `__str__` line; `icon` had
  zero references outside its own field definition and construction site). `NodeDef.name` and
  `NodeDef.customicon` are the only surviving fields, and `Profile.__parse_profile__`
  (`profile.py:300-310`) populates each with a same-line fallback —
  `name=ndict.get("nls") or ndict.get("name")`, `customicon=ndict.get("icon") or
  ndict.get("customicon")` — so whichever raw key the source profile actually used, the result
  lands in exactly one field, never two. `desc` has no legacy counterpart and is unchanged:
  `ndict.get("desc")`. Originally scoped to `NodeDef` only, with `NodeProperty.desc` and
  `Cmd.desc`/`native` left as the same *kind* of gap for later — now closed too, see G-2 below
  and the `NodeProperty` update just above in §1.

- **[G-2] Resolved — `Cmd.desc`/`native` added for parity, confirmed unused.** Verified against
  `src/nucore/cmd.py` directly (not just the earlier research pass): the `Command` dataclass had
  no `native` or `desc` fields at all. Per the team, neither is used anywhere in the current
  code — added purely for parity with the spec, same pattern as G-1's `NodeDef` fields.
  Implemented: `Command` (`cmd.py:53-59`) now carries `desc: str | None = None` and
  `native: str | None = None`, and `Profile.__parse_profile__` (`profile.py:284-291`) populates
  both via `cdict.get(...)`. `NodeProperty.desc` (the property-schema counterpart flagged
  alongside this in G-1) was added the same way: `NodeProperty` (`nodedef.py:54-58`) now has
  `desc: str = None`, populated via `pdict.get("desc")` (`profile.py:251-256`). Both are `None`
  when the source profile omits them, verified end-to-end for present/absent cases; full test
  suite continues to pass. **`native` specifically, per the team**: across multiple real
  profiles inspected, `native` has never been observed as anything but `"false"` — consistent
  with, though not proof of, it being vestigial rather than a flag that ever meaningfully
  varies. Kept as an open gap rather than resolved: the field is parsed and stored, but whether
  it's still load-bearing anywhere in PG3/IoX (vs. a relic no longer read on that side either)
  is unknown from this repo alone. `CommandParameter.desc` (the parameter-level counterpart —
  distinct from `Command.desc`) was **not** part of this change and remains open if wanted.

- **[G-3] Resolved — `Editor.is_reference` is dead in the current codebase.** The original
  intent (per the team) was to let an editor be a lightweight *pointer* to a shared/global
  editor definition — `id="X", is_reference=True` — instead of repeating that editor's full
  range list inline every time it's referenced, so callers/prompt output would know "look this
  editor id up elsewhere" rather than getting (or needing) the ranges right there. That's still
  visible in the branching logic — `get_python_description`, `get_json_descriptions`, and
  `write_descriptions` (`editor.py:259,273-275,296-299`) all special-case `is_reference=True`
  into a `REFERENCE editor id=...` placeholder instead of real range data. **But nothing in the
  current codebase ever sets it to `True`.** The only place an `Editor` is constructed from real
  profile data, `Profile.__build_editor__` (`profile.py:126-157`), hardcodes
  `Editor(id=edict["id"], is_reference=False, ranges=ranges)` (`profile.py:156`) — confirmed the
  single construction site in `src/`. Every test that builds an `Editor` fixture does the same
  (`is_reference=False`, e.g. `tests/unified/handlers/test_routine_automation.py`,
  `test_send_command_multi_param.py`, `test_get_routine_details_enum_labels.py`,
  `tests/rag/test_profile_rag_formatter_device_python.py`) — none exercise the `True` branch
  either. So today every editor is always fully inlined; the "global editor" indirection this
  field was built for isn't wired up to anything that would ever populate it, in either the
  Dynamic Profiles ingestion path or the native profile catalog path. It's a required
  constructor field carrying a constant value, and the four `if self.is_reference` branches in
  `editor.py` are currently unreachable in practice. **See G-4**: the same removed feature is
  also the origin of `Range.id`, with the retirement documented directly in source.

- **[G-4] Resolved — `Range.id` exists on both range types, but is dead weight: never anything
  but a copy of the parent `Editor.id`, and read in only one dormant, untested place.** Confirmed
  directly: both `EditorMinMaxRange` and `EditorSubsetRange` (`editor.py:158,25`) carry `id: str`
  (labeled `#editor id` in the source), and `Profile.__build_editor__` always sets it to
  `edict["id"]` — the *editor's* id — on every range it builds (`profile.py:138,150`). A range's
  `id` is therefore never anything other than a duplicate of its enclosing `Editor.id`; nothing
  in the parsing code gives a range an identity of its own. Matches the Dynamic Profiles spec,
  whose `Range` table has no `id` field at all — only `Editor` does.
  - `EditorMinMaxRange.id` is **never read anywhere** — none of its own methods reference
    `self.id`, and a repo-wide search for external `.id` access on a range object found nothing.
  - `EditorSubsetRange.id` **is** read, in exactly one place: `write_description()`
    (`editor.py:82`), `f"{self.id}_{self.uom.label}"`, and only when the range's UOM is an
    enumeration type (UOM 25/146/148). That branch is only reachable via the YAML-style prompt
    path (`write_descriptions`/`write_description`, gated by `json_output=False`) — which is
    **not** the CLI default (`--json-output` defaults to `True`, `run_unified_runtime.py:191`),
    **not** what the one other hardcoded call site uses (`routine_automation.py:175`:
    `json_output=True`), and **not exercised by any test** (zero tests call
    `write_description`/`write_descriptions` at all). Live code, but dormant in current practice
    — and even here, `self.id` is always identical to the enclosing `Editor.id`, so nothing is
    actually gained by reading it off the range instead.
  - **Root cause, and the same one as G-3**: `src/rag/profile_rag_formatter.py:222-231`
    documents it directly — editor/range ids existed for a **"DedupeDevices" cross-device
    editor-sharing pass**, since **removed as dead code** ("nothing performs that dedup any
    more"). This is the same removed feature G-3's `is_reference`/`REFERENCE_DELIMITER`
    mechanism was built for — both fields are remnants of one retired dedup system, not two
    unrelated gaps. The same docstring also records a **real bug** this caused: exposing the
    editor id in Python-literal output led a routine to author `param(id=<editor_id>, ...)`
    instead of the real parameter id, "because the two looked equally id-shaped" — which is
    exactly why `_editor_dict` (the current Python-literal rendering path,
    `profile_rag_formatter.py:222-234`) now deliberately excludes it.

- **[G-5] Resolved (platform knowledge, not derivable from this repo alone).**
  `plugin_model.md`'s `Profile` object is flat: `{nodedefs[], editors[], linkdefs[]}` — no
  family or instance anywhere in it. `nucore_domain_model.md`'s catalog model is
  `Family → Instance → {nodedefs, editors, linkdefs}`, and every live node resolution depends on
  a `(node_def_id, family, instance)` triple. **`family`** is a fixed constant for every
  plugin-originated device: unconditionally `family = "10"` (`DEVICE_FAMILY_PLUGIN`, defined at
  `src/iox/iox_definitions.py:78` and named `"Plugin"` in the `DEVICE_FAMILIES` display-name
  table at line 86). **`instance`** is assigned by the NuCore/IoX platform itself at runtime,
  not by the plugin and not by nucore-ai's Python code — when a plugin is installed, it's
  installed into an **instance slot**. Usually there's exactly one slot (one `instance` value)
  per plugin, but the platform supports installing multiple instances of the same plugin
  side by side (rare in practice), each occupying its own slot with a distinct `instance` value
  — which is exactly what the `(node_def_id, family, instance)` lookup key needs to disambiguate
  between them. This assignment happens hub/firmware-side; there's no "slot" concept anywhere in
  this repo's source (confirmed by a repo-wide grep) because nucore-ai's Python code never
  computes it — like every other family, it only ever reads back whatever `instance` value the
  hub has already assigned, the same way it does for native INSTEON/Z-Wave/Zigbee/Matter
  devices (`nucore_domain_model.md` §12).

- **[G-6] Corrected — "plugin lifecycle" is not any of the three docs in this folder; it's a
  separate develop→publish→install pipeline, two-thirds of it undocumented here.** The first
  draft of this section mislabeled item (a) below "marketplace lifecycle management" and treated
  that as *the* lifecycle. Per the team, that's wrong — the actual lifecycle has nothing to do
  with `runtime_plugin.md`, `plugin_model.md`, or the AI-tool-calling surface as a set; it's
  simply:
  - **Developer side**: (1) develop; (2) test locally — publish to a local plugin store on the
    developer's own machine; (3) publish to the production store. Steps 2-3 each get their own
    future document — **neither exists in this folder today**, and nothing here should be read
    as describing them.
  - **Customer side**: install (may require payment), configure, start, stop, restart, delete —
    this part *is* covered, by `runtime_plugin.md` §2/§4 (the REST/tool wiring for those six
    actions) — see Stage 0-1 in §3 above for the full picture.

  Separately — still true, but a **different observation**, not the lifecycle — this codebase
  has three distinct plugin-related API surfaces that don't get unified anywhere else: (a) the
  customer-side action wiring just described (`design/iox_apis/plugins-api.csv`, `IoXWrapper`,
  `plugin_management.py` tools); (b) Dynamic Profiles (`plugin_model.md`) — a plugin's own
  backend defining its device model to PG3/IoX, which nucore-ai never calls directly, only reads
  the result of via the standard profile/node load (Stage 3-4 in §3); (c) the AI-capable-plugin
  path (`/api/plugin/{id}/prompt`+`/tools`+`/request`) — nucore-ai calling *into* a plugin for
  LLM-tool-calling, layered independently on top of (a) and orthogonal to (b).

  **(c) is now resolved, not a mystery extension**: it's the AI-capable-plugin manifest
  declaration described in the new §1 entry and Stage 5b in §3, fully explained by
  `system_prompt.md`'s PLUGIN WORKFLOW section (`:167-197`) — a plugin declares `prompt`+`tools`
  in its deployment manifest, nucore-ai discovers this per-plugin at runtime via
  `get_plugin_capabilities`, and falls back to it when no existing tool can satisfy a request.
  It genuinely doesn't appear in the Dynamic Profiles documentation because it's a separate
  manifest concern from the device-model JSON that doc covers — not an undocumented PG3/IoX
  capability. Per the team, this is proven working in production today (the hebcal plugin is a
  real, already-implemented example), not a proposal.

- **[G-7] Partially resolved — "native links" is precisely defined and well understood;
  `Cmd.native` remains the unresolved half.** "Native" is overloaded across two unrelated
  concepts:
  - **"Native links"** — fully clear, confirmed in code, not just doc prose. `src/nucore/
    group.py:19-35` defines a `Linktype` enum with four values, each with an exact docstring:
    `LINK_TYPE_NATIVE` = "a direct link between the controller and the responder (e.g. Insteon
    links, Z-Wave associations, etc.), Controller→Responder" — i.e. a real protocol/hardware
    link where NuCore/IoX isn't in the data path at all — versus `LINK_TYPE_DEFAULT`
    (Controller→NuCore→Responder, command forwarded as-is), `LINK_TYPE_COMMAND` (same, but
    NuCore substitutes the command specified in the link), and `LINK_TYPE_IGNORE` (no link).
    This is surfaced directly to the LLM, not just internal: `src/unified/tools/
    tool_group_get_detail.json`'s description explicitly lists "link type per target
    (native/default/command/ignore)". Matches `plugin_model.md` §3's LinkDef description
    exactly.
  - **`Cmd.native`** — still unclear. No connection to the `Linktype` enum above was found
    anywhere in the codebase (different module, different concept, no shared code path); its
    meaning as a per-command flag remains open (see G-2 — added for parity, still unused, never
    interpreted anywhere). Per the team, across multiple real profiles inspected it has never
    been observed as anything but `"false"` — worth keeping the gap open rather than assuming a
    meaning, but it may simply no longer be a live flag on the PG3/IoX side either.
  These are two unrelated uses of the same word — the links sense is settled, the Cmd sense is
  not. Don't conflate them when reading either doc.

## 5. Quick index — "where do I find out about X?"

| Question | Doc / section |
|---|---|
| What's the actual plugin lifecycle (dev → publish → install)? | §3 Stage 0-1 above, and **[G-6]** in §4 |
| How do I write a plugin's device model from scratch? | §2 above, then `plugin_model.md` §2-3 |
| What does `NodeDef`/`Property`/`Command`/`Editor`/`LinkDef` mean inside nucore-ai's Python code? | `nucore_domain_model.md` §2-11 |
| How does a customer's raw value become a wire command? | `nucore_domain_model.md` §10-11 |
| How does nucore-ai install/list/configure a plugin? | `runtime_plugin.md` §2-4, §6 |
| How does nucore-ai call a plugin's own LLM tools? | `runtime_plugin.md` §5, and §3 Stage 5b above |
| What's the `format` mini-language for program/scene display? | `plugin_model.md` §4 |
| How do I migrate a plugin off static profile files? | `plugin_model.md` §5 |
| What family code is a given protocol? | `nucore_domain_model.md` §12 |
| What's still unbuilt / where is this headed? | `runtime_plugin.md` §8 |
| What don't we actually know yet? | §4 above (G-1 through G-7) |
