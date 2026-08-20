
> **Superseded.** The router -> intent-handler architecture described in `# BACKGROUND`/`# ROUTER`
> below, and `src/intent_handler_directory/`/the classic parts of `src/intent_handler/` it refers
> to, have been retired. The "UNIFIED PROMPT + TOOLS" proposal further down is no longer a parallel,
> additive path -- it's the only runtime (`src/unified/`). Kept here for historical context only.

# PURPOSE
This document describe design decisions for a nucore llm integration

# BACKGROUND
Nucore is a smart home automation controller/platform. It offers many services the most important of which are:
- Command/Control devices and get thier status
- Monitor devices
- Manage `groups` and `scenes`
- Manage automation `routines`

Since a large installations may exhuast the context immediately (device names, ids, supported commands/params/tc.), the design uses a rotuer -> intent-handler architecture.

# ROUTER
The role of the router is to:
Use the summary device/routine information embedded in its system message in conjuction with the user query to:
1. If the query can be answered from device/routines databases, do it in Natural language and return
2. If not, if it can be categorized as an intent,
- create an array of intents + associated candidate devices/routines for each intent
3. Otherwise, respond with natural language

Always store the conversation history.

---

# UNIFIED PROMPT + TOOLS (PROPOSAL, 2026-07-15)

## Why

Today's dispatch is router (1 LLM call, picks intent(s) + a `route_plan`) -> one LLM call per
intent-handler step -> a final synthesis LLM call to turn tool results into a human answer.
That's 3+ LLM calls for anything requiring action, a bespoke `route_plan`/`route_context`
threading mechanism (`runtime.py`), a bespoke pending-clarification classifier
(`_classify_pending_continuation`), and 8 separate intent prompts (some duplicating the same
DEFINITIONS/GLOBAL RULES boilerplate). None of this is unique to NuCore's domain — it exists
because the router/intent-handler split was invented before assuming the LLM could reliably
carry a whole device catalog *and* decide + execute action in one native agentic tool-use loop.
Now that DEVICE DATABASE is cheap (Python-literal, deduped — see the editor-refs work), that
assumption is worth re-examining.

Goal: one system prompt, one set of tools, using the *native* multi-turn tool-calling loop every
major LLM provider (Claude, GPT-4/5, Gemini) already trains on — model calls a tool, gets a
result, decides whether to call another or answer — instead of a bespoke pre-planned
`route_plan`. Existing intent-handler directories stay as-is, unchanged, for third-party/
non-native-tool-calling integrations.

## LLM-friendly system description (the concept glossary)

This is `definitions.md`'s content, tightened and reorganized around the four capability
groups the user asked about, meant to be the *entire* domain-knowledge section of the unified
prompt (no separate per-intent restatement):

**Devices, Properties, Commands** — A `device` is one node. It has `properties` (its current
readable state — status, temperature, brightness) and two kinds of `commands`: `accepts`
(things you can tell it to do — on/off/dim/set-setpoint) and `sends` (things it tells NuCore —
motion sensed, a button pressed). Every property and every command parameter is constrained by
an `editor` (its valid range, unit, or enumerated choices) — but the *unified* prompt should
never require the model to read raw `uom`/`precision` integers itself (see "Value resolution"
below); it just needs to know a property/command exists and roughly what kind of value it takes.

**Groups and Scenes** — A `group` is any set of devices that act together. Membership has a
role: `controller` (issues commands) or `responder` (reacts). A `scene` is the specific case
where NuCore itself is the controller and every member is a responder — "activate this scene"
just means "NuCore sends On to every member." Two devices can also be directly `cross-linked`
(each is the other's controller) independent of any NuCore-owned scene — that's native
device-to-device linking, not something NuCore mediates.

**Routines** — An if/then/else automation: a condition (device state, time, schedule), a `then`
branch, an `else` branch. Routines have both *content* (what logic they run) and *runtime state*
(enabled/disabled, currently running, scheduled-to-run-at-startup) — those are different
questions ("what does this routine do" vs. "is this routine currently active") and should stay
different tools/concepts, matching today's `routine_automation` vs. `routine_status_ops` split.

**Everything else** (plugins, marketplace extensions, UD Mobile push) is package-manager-shaped
lifecycle CRUD over a named resource — install/update/uninstall/list, or start/stop/configure.

## Proposed tool catalog

Grouped by capability, matching the survey findings on which handlers are already CRUD-shaped
vs. which need care:

- **Query tools** (read-only, safe to call speculatively): `search_devices(query)` (large-install
  escape hatch only — see "Context strategy" below; not needed when the compact `DEVICE DATABASE`
  already fits in the system prompt), `get_device_detail(device_id)` (full editor fidelity, lazy —
  see below), `get_property(device_id, property)`, `search_routines(query)`,
  `get_routine_detail(id)`, `list_groups`/`get_group_detail(id)`.
- **Action tools**: `send_command(device_id, command, value?)`, `node_op(node_id, operation,
  ...)` (enable/disable/rename/move/delete/add_folder/add_group — direct port of `node_ops`),
  `routine_status_op(id, operation)` (direct port of `routine_status_ops`), `group_scene_op(...)`
  (direct port of `group_scene_ops`'s add/update/remove-member trio; the "multi-device scene"
  saga stays a separate compound tool, not collapsed into a verb — it's genuinely an
  orchestration, not an action).
- **Memory tools**: `remember(section, entry, key?)`, `recall(query?)`, `forget(id_or_key)` —
  direct simplification of `intent_memory`'s single CRUD tool into named verbs, since "what verb
  is this" is exactly the kind of thing native tool-calling models are best at picking without
  an `action` enum param.
- **Routine authoring**: `create_or_update_routine(name, id?, code)` — kept as its own tool,
  unmerged from the others (see "What stays semi-isolated" below).
- **Package management**: `manage_extension(action, extension_id, ...)` and
  `manage_plugin(action, plugin_id, ...)` — same CRUD shape, and `plugins_management`'s handler
  bug (wrong tool-name check, missing manager method, enum actions with no backend) needs fixing
  regardless of which architecture wins; it's not a consequence of this redesign.

## Context strategy: search-then-detail, not always-inject-everything

The reason the router/intent-handler split exists at all is context size. A unified prompt
doesn't get to ignore that — it just gets to solve it with a pattern every LLM already
understands instead of a bespoke classifier:

- The system prompt **always** carries the compact `DEVICE DATABASE` (Python-literal,
  deduped, name/profile/commands/enum-labels only — this is already what the router carries
  today, so if it fits there it fits here) for the *whole* inventory, not just candidates.
- Full editor fidelity (uom/min/max/precision) is **not** preloaded. `get_device_detail(id)` is
  a tool call the model makes only for a device it's about to act on — the same "search index,
  then fetch the one document" pattern used for web search or code-search tools, which every
  major model already has extensive native training on. This also directly replaces
  `DedupeDevices`'s batch-level editor sharing — with detail fetched per-device on demand, the
  editor-sharing optimization matters less (though the `EDITORS`/`PROFILES` split from the
  device-schema-editor-refs branch is still the right shape for whatever `get_device_detail`
  returns).

### Does candidate-device-finding still need a separate step?

Today the router's whole job is one LLM call that reads the compact `DEVICE DATABASE` plus the
query and picks candidate device ids (`ROUTER` section above) — a step that exists only because
the router/intent-handler split assumed the model shouldn't carry the full catalog *and* decide/
act in the same pass. In the unified design that assumption is gone: the compact `DEVICE
DATABASE` is *already* sitting in the same context as the tool-calling turn, so for a normal-size
installation, candidate-finding isn't a mechanism anymore — it's just the model reading names/
profiles/commands it can already see and putting the right id(s) into its `send_command`/
`get_device_detail` call, the same way it already resolves simple lookups directly today without
any tool call. No separate call, no separate prompt section.

The one case where this doesn't hold is an installation large enough that even the deduped
compact `DEVICE DATABASE` exceeds the context budget — which is the literal reason the router/
intent-handler split exists at all (see `BACKGROUND` above). For that case, `search_devices(query)`
stays as an escape hatch, but it must be a **deterministic backend lookup** (substring/token match
over device names and profiles in Python, no LLM involved), not a hidden classification call —
otherwise it just reconstructs today's router under a new name and reintroduces the extra LLM
call this design is trying to remove. Whether real installations actually get large enough to need
this hasn't been measured yet; worth checking actual device-count distribution before deciding
whether `search_devices` ships in v1 or gets deferred until it's shown to be necessary.

### Device ids are never backend-resolved from names — only command/property ids are

Everything below applies to command/property/parameter ids only. Device (and group/scene) ids
must always be real ids the model reads directly out of `PROFILES[...]['devices']` and passes
through verbatim — never a name the backend tries to resolve. Two reasons this doesn't extend the
same way command/property names do:
- **It's the scope anchor.** The exact-match-or-reject argument below only holds because
  resolution is scoped to one already-known-correct `device_id`; if the device itself were also
  resolved by fuzzy/backend name matching, there'd be no guarantee `'On Level'` is even being
  looked up against the right device's namespace in the first place.
- **Device names collide far more than command/property names do within one profile** — large
  real installations have duplicate/near-duplicate device names (multiple similarly-named units
  across rooms) in a way a single device's own command/property list generally doesn't.

This isn't a new constraint — `PROFILES[...]['devices']` entries already always carry the real
device id (`dedupe_profiles.py:307`), and every tool signature in this proposal already takes
`device_id`, never a name. Stating it here so it doesn't erode later.

### Command/property ids: resolved by the backend from names, not carried in the prompt

The compact `DEVICE DATABASE` has never carried command/property/parameter ids — only display
names and enum labels (`minimal_rag_formatter.py:53-95` collects names, never ids). Two options
were weighed for how a `send_command`/`get_property` call gets a real id:

1. Add ids into the always-loaded compact `DEVICE DATABASE`. Measured cost: +7.5% (+687 tokens on
   a real 176-device installation captured this session) — cheap and cache-amortized, but a
   permanent tax on every session regardless of whether it's ever used.
2. Require a `get_device_detail` round-trip before every action to fetch real ids. Backend compute
   for this is free (it's just re-rendering `Editor`/`NodeDef` objects already in memory) — but it
   still costs one full extra model inference turn, on every single action, even parameterless ones
   like "turn off the light."

Neither is necessary. The model already emits the *name* it read out of the compact `DEVICE
DATABASE` (verbatim, since that's the only representation it's ever shown) — the backend resolves
that name to a real id itself, deterministically, using the `NodeDef`/`Editor` objects it already
holds for that specific `device_id`. No prompt bloat, no extra round-trip for the common case.

**Why this doesn't reopen the safety gap the id-only rule exists for:** the risk isn't the model
picking the *wrong* real command (a reasoning error, identical whether the model emits a name or
an id — no representation choice fixes bad judgment). The risk is the model's output not cleanly
matching *anything* real (typo, paraphrase, case drift) and the backend silently resolving that
near-miss to a similarly-named-but-wrong item. That risk is real data, not hypothetical: the real
installation captured this session has `DimmerLampSwitch_ADV` using `'On Level'` as *both* a
property name (`pc_2`) and an accepts-command name (`ac_2`) — same device, same string, two
different underlying ids. It resolves cleanly for free because `get_property`/`send_command` are
different tools, so the tool itself already scopes which namespace `'On Level'` means; no
ambiguity reaches the backend matcher at all.

The rule that keeps this safe: resolution must be **exact match** (case/whitespace-normalized),
**scoped to `(device_id, that tool's namespace)`**, with a hard reject-and-request-clarification on
anything that doesn't match — never a fuzzy/similarity fallback. Fuzzy matching is where a
confident-but-wrong command (e.g., "Off" resolving to "Fast Off") could execute silently against
real hardware, which is a worse failure mode than a wrong numeric value since there's no
range-check to catch it. With strict matching, this degrades exactly as safely as an invalid id
does today (`common.md`'s existing "if missing/invalid, request clarification" rule extends
unchanged) — and since the compact `DEVICE DATABASE` already shows the model the exact name string
to copy, there's no more reason to expect drift here than there would be copying an id verbatim.
`get_device_detail` stays reserved for what it's actually needed for: editor fidelity (uom/
precision/enum keys) for numeric/enum-valued commands, not id lookup.

## Value resolution: split between the LLM (parsing) and the backend (arithmetic), not moved wholesale

`common.md` today spends four whole sections (`GLOBAL UOM RULES`, `GLOBAL PRECISION RULES`,
`GLOBAL CUSTOMER VALUE CONVERSION RULES`, plus half of `GLOBAL ID RULES`) forcing the model to
be a manual uom/precision/enum-key lookup engine — "never guess a uom," "copy precision exactly,"
a 4-case value-conversion decision tree. This is real bespoke prompt engineering, but it conflates
two different jobs that need to stay split:

- **Parsing natural language into a structured value is an LLM job, not a backend job.**
  "A billion dollars," "kinda warm, maybe low 70s," "Program Auto" all require language
  understanding — recognizing scale words, matching a customer's phrasing to one of several
  enum labels, deciding when something's too ambiguous to act on. A backend can't regex its way
  through that; only the model reading the sentence can. An earlier version of this proposal
  described passing the customer's raw string (`"a billion dollars"`) straight to the backend
  for "unit parsing" — that was wrong, and would have required the backend to reimplement NL
  understanding it doesn't have.
- **Converting a structured value is a backend job, not an LLM job.** Once the model has
  extracted "the customer means 1,000,000,000 USD" or matched "Program Auto" to the editor's
  literal enum label, the remaining work — USD→cents arithmetic, F↔C conversion, rounding to
  the editor's exact precision, min/max range validation, confirming a picked enum label exists
  in that editor's table and resolving it to its key — is exact, closed-world, and exactly where
  LLMs are unreliable (arithmetic slips, invented conversion factors, hallucinated enum keys).

Revised proposal: `send_command(device_id, command, value?, unit?)` — the model supplies the
*parsed* value and, when the customer stated one, the unit or enum label it understood
(`value=1000000000, unit="USD"`; `value=72, unit="F"`; `value="Program Auto"` for an enum
command). The backend (which already has every device's real `Editor` objects, per the
device-schema-editor-refs work) does the conversion, precision rounding, range validation, and
enum-key resolution deterministically in Python against that specific editor's table, and
returns a clear error string as the tool result if the unit is unconvertible, the label doesn't
match, or the value is out of range — which the model then relays to the user or asks a
follow-up question about, exactly like it already handles any other tool error. The backend
never receives or interprets free-form customer text; it only ever receives numbers and short
tokens the model already resolved. This still deletes most of `common.md`'s UOM/precision
arithmetic rules (the model no longer hand-computes conversions or copies precision digits) while
keeping natural-language interpretation where it belongs.

## Multi-turn orchestration: native tool-use loop, not `route_plan`

Replace the router's `route_plan` (a full plan decided in one shot, then executed step-by-step
by `runtime._execute_route_plan`) with a standard agentic loop: call a tool, get a result, decide
the next tool call or produce a final answer, repeat. This is what `tools` parameters on every
major provider's chat-completions API are designed for, and removes several bespoke mechanisms
entirely:
- `RoutePlanStep`/`route_context`/`step_contexts` envelope threading (`runtime.py:1010-1051`) —
  unneeded; tool results are already in the conversation the model sees on its next turn.
- `_classify_pending_continuation` (a whole separate LLM call just to guess whether a new
  message continues a pending clarification) — unneeded; the model already has the prior turn's
  tool call/result in context and can naturally decide to continue or start fresh, the way any
  agentic conversation already works.
- `intent_memory`'s cross-intent `get_memory_context` hydration plumbing — simplifies to either
  a normal `recall` tool call, or memory entries folded directly into the always-on system
  prompt.

Tradeoff to flag honestly: this trades the router's one-shot, fully-deterministic plan for
multiple sequential round-trips (latency, token cost per turn) when a query genuinely needs
several tool calls. Parallel tool-calling (most providers support calling 2+ tools in one turn
when they're independent) claws back some of that for the common "do these two things" case.

## What stays semi-isolated: `routine_automation`'s DSL

`routine_automation`'s prompt is not a tool-usage guide, it's a ~300-line language spec (grammar,
invalid-pattern list, five worked examples) for a constrained Python-like DSL that
`routine_compiler.py` parses via `ast`. Folding that instruction volume into the always-on
unified system prompt risks it competing for attention with every other tool's instructions on
every single call, even ones that never touch routines. Recommendation: keep
`create_or_update_routine`'s DSL spec attached to *that tool's own description/instructions*
(most providers support long, detailed per-tool descriptions) rather than the shared system
prompt, so it's only "in view" density-wise when the model is actually reasoning about that
tool — the closest native equivalent to today's isolation without reintroducing a second prompt.

## What this does NOT change

- Existing `intent_handler_directory/*` stays exactly as-is — this is a *new, parallel* entry
  point for native-tool-calling clients, not a replacement. Third-party integrations that expect
  today's router -> intent-handler contract keep working unmodified.
- Device-schema-editor-refs' `EDITORS`/`PROFILES`/`DEVICES` split is reused as-is for whatever
  `get_device_detail` returns — this design builds on that work, doesn't redo it.
- `group_scene_ops`'s real server-side validation (controller/responder role checks, link-type
  compatibility) stays in Python regardless of architecture — that's business logic, not prompt
  scaffolding, and belongs in code either way.

## Independent finding (not part of this proposal, worth a separate fix)

`security.md` (prompt-injection defense rules) is loaded into the common-module cache but is
**not referenced by any `<<...>>` placeholder in any current `prompt.md`** (router or intent
handler) — it appears to be dead code, never actually included in a live prompt. Worth fixing
regardless of which architecture direction is chosen.

## Pros / Cons

**Pros:**
- Fewer LLM calls per action (1-2 vs. 3+ today: router call + intent call + synthesis call).
- Removes several bespoke mechanisms (`route_plan` threading, pending-continuation classifier,
  cross-intent memory hydration plumbing) in favor of patterns every major LLM already handles
  natively — directly serves the "any off-the-shelf LLM should understand this" goal.
- Moves unit-conversion arithmetic, precision rounding, range validation, and enum-key lookup
  out of LLM-generated text into deterministic backend code, while leaving natural-language
  parsing (what did the customer actually mean) with the model, where it belongs — a real
  reliability improvement, not just a simplification.
- One glossary/definitions section instead of near-duplicate boilerplate across 8 prompts.
- Search-then-detail context strategy scales to large installations the same way the router
  already does, without needing a separate hidden classification call to build a candidate list.

**Cons:**
- Multi-tool-call queries cost more round-trips/latency than a pre-planned `route_plan`
  executed server-side in one pass (partially mitigated by parallel tool-calling).
- `routine_automation`'s DSL is a genuinely different capability (code generation) from every
  other tool (structured action calls) — even with per-tool-description isolation, it's the
  biggest risk of prompt-quality regression in a unified system, and needs real evaluation
  before trusting it outside its current dedicated prompt.
- `group_scene_ops`'s multi-device "saga" tool doesn't reduce cleanly to a single verb — either
  it stays a compound tool (fine) or its orchestration logic has to move client-side into
  multiple simpler tool calls the model sequences itself (behavior change, needs testing).
- This is new surface area (a new prompt, a new tool set, a new dispatch loop) running
  *alongside* the existing one — real engineering cost to build and validate, not a drop-in
  replacement, and both paths need to be kept correct going forward until/unless the old one is
  retired for first-party use.
- Losing the router's upfront full-plan visibility means losing today's single point where a
  whole multi-step plan could be logged/audited/short-circuited before any tool executes.
