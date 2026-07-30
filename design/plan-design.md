# PLAN FEATURE (PROPOSAL, 2026-07-30)

## Purpose

This document captures the design for a new **Plan** feature: an LLM-driven session, structurally
parallel to the existing INSTEON/Z-Wave/Zigbee/Matter **Diagnostics** feature
(`src/iox/diagnostics/`), but for *configuring* a system instead of investigating one. Where
Diagnostics reads live/replica state and converges on a text conclusion, Plan proposes and then
commits real changes to a customer's NuCore installation: devices, folders (rooms), scenes,
automations, and variables.

No implementation exists yet. This document exists to align on architecture before any code is
written.

## Relationship to Diagnostics

Reuses the same session shape:

- `start_plan(plan_type)` -- analogous to `start_diagnostics()`. Loads a shared mechanics preamble
  (how staging/tiered-commit/review/revise works -- written once) concatenated with the chosen
  `plan_<type>.md` file's own guidance and step catalog.
- `run_plan_step(step, params)` -- analogous to `run_diagnostic_step`. Same "one step at a time,
  never several in the same turn" rule Diagnostics already enforces, for the same reason: staged
  operations have real ordering dependencies (a device must exist before a scene references it; a
  scene must exist before an automation references it), and some steps drive real hub/PLM
  hardware that can't run two operations at once.

## Why Plan is architecturally different from Diagnostics: commit risk

Diagnostics is read-only -- every step is a safe, repeatable query, and the only thing that
"changes" is a text conclusion. Plan's steps create and modify live configuration, which is a
different risk profile: harder to reverse, and directly touching a system the customer already
depends on for their home.

**Decision: hybrid commit tiering.**
- Low-risk, easily-reversible operations (creating a folder, adding an already-paired device to a
  room) execute immediately when the step is called, the same way Diagnostics steps run
  immediately.
- Higher-risk or bulk operations (creating scenes, creating automations, anything that could
  touch an existing working configuration) are **staged** first: the LLM builds a proposed
  change list via `propose_*` steps, renders it back to the customer in plain language via
  `review_plan`, revises it on feedback via `revise_plan`, and only commits via an explicit
  `apply_plan` step.

**Decision: separate prompt/step-catalog file per plan type**, not one shared `plan.md`. Unlike
Diagnostics' four subsystems (which all share one reasoning model -- link tables, controller/
responder roles), Plan's scenarios share almost no domain reasoning with each other (irrigation
zones vs. holiday dates vs. moving devices), so one shared file would just staple unrelated topics
together. The *mechanics* (staging/tiering/review/revise) are still written exactly once, in a
shared preamble every `plan_<type>.md` is concatenated with.

## Plan type catalog

From the original ask, brainstormed additions, and the three requested this round:

| Plan type | One-line description | Needs `pair_device`? | Needs plugin/feature check? |
|---|---|---|---|
| New installation | Customer describes devices, locations, desired scenes/automations from scratch; Plan adds devices, creates folders/scenes/automations. | Yes | Maybe (e.g. voice/media plugins) |
| New construction / empty shell | Like new installation, but zero prior NuCore config exists at all. | Yes | Maybe |
| Room addition / expansion | Onboard new devices into an *already-configured* house without disturbing existing scenes/automations. | Yes | Maybe |
| Vacation | Creates a "lived-in" look while the customer is away (randomized lighting, staggered schedules). | No | No |
| Holidays | Finds localized holiday dates and creates routines for them. | No | Maybe (calendar/date lookup) |
| Remodel | Devices/scenes/automations change or move as rooms are physically reconfigured. | Sometimes (new devices) | No |
| Move | Migrate a configuration from one house to another. | Yes | Maybe |
| Irrigation | Zone-based watering schedules, seasonal adjustment. | No | No |
| Rental/Airbnb turnover | Reset scenes/automations to a recurring "guest-ready" default between guests. | No | No |
| Aging-in-place / accessibility | Simplified scenes, motion-activated lighting, fewer manual steps. | No | No |
| Storm/outage prep | Generator-transfer awareness, safe-mode fallback scenes. | No | Maybe (generator/monitoring plugin) |
| Party/event mode | Temporary, auto-expiring scene set for a one-off gathering. | No | No |
| Downsizing/decommission | Cleanly retire a wing or whole house, archiving devices/scenes without orphaning automation references. | No | No |
| Nursery/new baby | Night-light routines, quiet/do-not-disturb windows. | No | No |
| Animal protection | Automations/scenes that respond to pet/animal presence or safety needs (e.g. temperature-triggered alerts, pet-safe lighting). | No | Maybe (sensor/camera plugin) |
| Safety and security | Motion/door/window-sensor scenes, alerting, camera-integration awareness. | No | Likely (e.g. Camect) |
| Serenity | Calming music/video with complementary lighting, on demand or scheduled. | No | Likely (e.g. YouTube for media) |

## AI-capable plugin contract (`get_tool(s)` / `get_prompt()` / `handle_llm_result()`)

Any plugin that wants to be usable by an LLM session -- a third-party marketplace plugin (Camect,
a hebcal-style calendar source, YouTube) or Plan itself (see "Should Plan types be plugins?" below)
-- implements the same three standard commands, modeled the same way a device already exposes
accepts/sends commands:

- **`get_tool(s)`** -- returns the tool.json-shaped schema(s) for whatever this plugin can do
  (name, params, description). A plugin can expose more than one capability; each capability's
  name is uniquified with the plugin's installation id (e.g. `{plugin_installation_id}_
  get_holidays`), which is what makes name collisions across multiple installed instances of the
  same plugin type immaterial -- no central naming registry needed.
- **`get_prompt()`** -- returns the full natural-language usage guidance for this plugin's
  capabilities, lazily loaded only once the plugin becomes relevant to the conversation (never
  preloaded for every installed plugin on every turn -- same rationale as `get_device_detail`'s
  lazy full-fidelity fetch, applied to plugins instead of devices).
- **`handle_llm_result(tool_result)`** -- the actual execution hook. Whatever the LLM produced
  when "calling" one of this plugin's declared tools (name + arguments) is forwarded here
  verbatim; the plugin's own implementation does the real work and returns whatever should go back
  to the LLM as that turn's tool result.

**No changes to the model-facing tool surface or to `AgenticLoop`/`loop.py` are needed.** This
reuses exactly the pattern Diagnostics already established with `run_diagnostic_step`: one fixed
dispatcher tool, a text-only catalog of what's callable (here, assembled from installed plugins'
`get_tool(s)`/`get_prompt()` output instead of a static prompt file), and application-level routing
(the equivalent of `_parse_diagnostic_config`/`getattr(self, f"_{step}")`) rather than a
dynamically-changing tool list. The dispatcher's only job is a naming-convention check: if a call's
tool name matches an installed plugin's id prefix, strip it and forward the whole `{name, args}`
payload to that plugin device's `handle_llm_result` command via the same generic device-command
path (`send_command`) every other device command already uses -- zero plugin-specific code in
core, for any plugin.

## Should Plan types be plugins?

**Decision: yes for the mechanism, no for the trust model.**

Plan types already share no more domain reasoning with each other than arbitrary third-party
plugins do (that's exactly why "separate file per plan type" was decided above), so reusing the
`get_tool(s)`/`get_prompt()`/`handle_llm_result` contract for plan types avoids building two
parallel lazy-loading/dispatch systems -- one for `plan_<type>.md` files, one for plugins. Under
this contract, a plan type's `get_prompt()` returns what would otherwise have been its
`plan_<type>.md` content; its `get_tool(s)` declares its `propose_*`/gather/commit steps; its
`handle_llm_result` executes them.

But a plan type's `handle_llm_result` needs privileged write access to core NuCore APIs --
`add_node`, `multi_device_scene`, `create_or_update_routine` -- that an arbitrary third-party
marketplace plugin (a buggy or compromised "YouTube" plugin, say) must never get just by
implementing the same three commands. So plan types are a **first-party, privileged tier** using
the identical discovery/loading contract, while ordinary third-party plugins stay sandboxed to
whatever narrow API surface they themselves declare and call back through. Concretely: plan
types' `handle_llm_result` implementations live in trusted core code with direct access to the
write APIs mapped below, whereas a third-party plugin's `handle_llm_result` runs in its own
process/sandbox and can only reach whatever its own backend chooses to expose.

## Architecture

### Where the backend lives

Plan's backend lives in `src/unified/planning/`, not `src/iox/`. Diagnostics lives under `src/iox/`
because it's fundamentally about hub/protocol state (link tables, PLM connectivity). Plan's core
operations -- folders, scenes, automations, variables -- are already `NuCoreInterface`-level and
protocol-agnostic (see mapping below); the one protocol-specific piece is device pairing, which
gets its own small per-protocol dispatch module underneath, the same way `insteon_diag.py` sits
under `iox_diagnostics.py` today. `src/unified/planning/` hosts both the shared staging/commit
engine (`propose_*`/`review_plan`/`apply_plan`) and each plan type's privileged `get_tool(s)`/
`get_prompt()`/`handle_llm_result()` implementation from the plugin contract above; third-party
plugins implement the same three-command contract entirely on their own, outside this codebase.

### Common step catalog (implemented once, shared by every plan type)

- **Gather**: `list_devices`, `list_folders`, `list_scenes`, `list_automations`, `list_variables`,
  plus the plugin/feature-capability steps described below.
- **Staging**: `propose_folder`, `propose_scene`, `propose_automation`, `propose_variable`,
  `review_plan` (renders the whole staged plan back in plain language), `revise_plan` (edit or
  remove a staged item).
- **Commit** (hybrid tiers): immediate steps (`create_folder`, `add_device` for an
  already-paired device) vs. `apply_plan` (executes every staged item, tier by tier, reporting
  per-item success/failure -- not all-or-nothing, since the underlying calls have real server-side
  validation that can fail per item, e.g. `group_scene_ops`' controller/responder role checks).
- **Terminal**: `conclude`, `stop` -- same semantics as Diagnostics.

### Staged-plan data model

A staged plan is a list of `{op, params, status}` entries held in session state:
`status` starts `"proposed"`, moves to `"applied"` or `"failed: <reason>"` once `apply_plan` runs.
`review_plan` renders this list in plain language for the customer; `revise_plan` mutates entries
in place (edit params, remove an entry) before commit. This mirrors the `_compare_links_files`-style
principle from Diagnostics of doing structural comparison/bookkeeping in Python, not asking the LLM
to track state across turns by re-reading its own prior messages.

### Mapping Plan operations to existing capabilities (grounded in code survey)

Most of Plan's execution layer already exists and just needs orchestrating:

- **Folders**: `NuCoreInterface.add_node(node_name, type="folder")` --
  `src/nucore/nucore_interface.py:342`, impl `src/iox/iox_wrapper.py:1135` (`POST /api/nodes`).
- **Rename/move/enable/disable/delete any node**: `node_ops(node_id, operation)` --
  `nucore_interface.py:352`, impl `iox_wrapper.py:1174`. Already exposed via
  `tool_node_op.json`/`src/unified/handlers/node_ops.py:37`.
- **Scenes/groups**: `multi_device_scene` (`src/unified/handlers/group_scene_ops.py:91`) already
  creates a new group *and* populates membership with controller/responder role prechecks in one
  call -- this is the step Plan's `apply_plan` should call for `propose_scene` entries.
  `group_scene_add_member`/`remove_member`/`update_link` (`group_scene_ops.py:44`) cover adjusting
  an *existing* scene (relevant to Remodel, Rental turnover).
- **Automations**: `create_or_update_routine` (`src/unified/handlers/routine_automation.py:431`),
  which already compiles a restricted-Python DSL via `routine_compiler.py` into the hub's
  trigger/condition/action schema, with name-to-id resolution and hub-error surfacing. Plan's
  automation-authoring steps (Holidays, Irrigation, Serenity, Security, Animal protection all need
  these) should reuse this DSL as-is.
- **Variables**: `variable_op` (`src/unified/handlers/variable_ops.py:80`).

Per `design/design.md`'s own "What stays semi-isolated" note about `routine_automation`'s DSL: its
~300-line grammar/spec should stay attached to that step's own tool description, not folded into
the shared Plan mechanics preamble -- same isolation rationale applies here unchanged.

### Plugin/feature-capability check (for Serenity, Security, Animal protection, etc.)

Grounded in a direct code survey of `src/unified/handlers/plugin_management.py`:

- **Already exists and is safe to call today**: `list_installed_plugins()` (line 56),
  `list_store_plugins()` (line 35), `list_purchased_plugins()` (line 77, which joins purchased
  license rows against the store list by `nsid` to resolve names) -- all read-only, all already
  exposed as unified tools.
- **Gap**: none of these take a name/id filter (`input_schema` is `{}` for both list tools), so
  "does plugin X exist" today means fetching the full list and matching client-side. There is also
  **no install capability at all** -- `NuCoreInterface.plugin_ops(plugin_id, operation)`
  (`nucore_interface.py:481`) declares `install`/`uninstall`/`status`/`details` in its signature,
  but the concrete implementation (`iox_wrapper.py:1473-1502`) only implements `start`/`stop`/
  `restart`; the rest fall through to `raise NotImplementedError` at line 1502. `configure_plugin()`
  (`iox_wrapper.py:1504`) is likewise an unimplemented stub. Neither method is registered in
  `TOOL_HANDLERS` (`src/unified/dispatch.py:32-50`), so no LLM tool can install, configure, or check
  status of a plugin today. (A previous plugin-management bug noted in `design/design.md` --
  wrong tool-name check, missing manager method -- is now moot: that whole handler was deleted in
  commit `037a700` along with the retired `intent_handler_directory` tree; the current read-only
  replacement never reintroduced install/configure.)

**Design for Plan**: no new search/filter tool. Matching a customer's desired capability (e.g.
"calming music," "camera-based detection") against a plugin's name/description is a natural-
language judgment call, not a string match -- there's no canonical capability vocabulary to search
by, and a customer's phrasing will rarely equal a plugin's literal name. Building a "search" step
would either find nothing (no query term matches) or have to be a fuzzy matcher over free text,
which is exactly the unreliable resolution pattern the rest of this codebase already rejects for
command/property-name resolution (see `design/design.md`'s "exact match ... never a fuzzy/
similarity fallback" rule). Instead, Plan's step catalog feeds the LLM the existing tools' full
results and lets it reason over them directly, in this fixed order:

1. Read `list_installed_plugins` (`plugin_management.py:56`). If a plugin's name/description
   plausibly already covers the needed capability, it's available -- nothing to stage.
2. If nothing in installed matches, read `list_purchased_plugins` (`plugin_management.py:77`). If
   a match is found there, the customer already holds a license but hasn't installed it -- stage
   an `install_plugin(plugin_id)` step. This is a stub in the same sense as `pair_device`:
   `plugin_ops`'s `install` action exists in the interface signature (`nucore_interface.py:481`)
   but raises `NotImplementedError` in the current implementation (`iox_wrapper.py:1502`) -- the
   step is built now so the hook exists, even though it can't actually execute yet.
3. If still no match, read `list_store_plugins` (`plugin_management.py:35`, the full
   marketplace). A match here means the customer doesn't own it at all -- there is no purchase API
   anywhere in the surveyed code, so this case can only ever be a customer-facing recommendation
   ("you'd need to purchase X from the marketplace"), never a stub call, unlike case 2.

Every plan type that leans on a plugin (Serenity, Security, Animal protection) should follow this
three-step order and land on the narrowest applicable outcome -- already available, install-stub,
or recommend-purchase -- rather than assuming it can just make the capability appear.

### Device pairing (explicitly stubbed, per this round's decision)

Only New installation, New construction, Room addition, and Move need this. Grounded in survey:

- `IoXSOAPAction` (`src/iox/iox_definitions.py`) already defines `SOAP_TYPE_ADD_NODE` (line 30),
  `SOAP_TYPE_DISCOVER_NODES` (line 31), `SOAP_TYPE_CANCEL_NODES_DISCOVERY` (line 42), and
  `SOAP_TYPE_SET_DEVICE_LINKING_MODE` (line 43, currently never called anywhere). Today these are
  only invoked from **private** diagnostics steps (`_add_node`/`_discover_nodes`/
  `_cancel_nodes_discovery`, `src/iox/diagnostics/iox_diagnostics.py:544-553`) -- not exposed as a
  unified tool.
- **Decision**: build a `pair_device(protocol, ...)` step now. For INSTEON, it can genuinely put
  the PLM into linking mode using the existing SOAP actions above and tell the customer to press
  the device's set button -- that's real capability, not a fake stub. For Z-Wave/Zigbee/Matter, no
  equivalent primitives exist anywhere in `iox_wrapper.py`; the step returns "not yet supported,"
  and the relevant `plan_<type>.md` files instruct the LLM to fall back to walking the customer
  through the vendor's manual pairing procedure conversationally instead. Either way, a device only
  becomes eligible for `propose_scene`/`propose_automation` once `list_devices` confirms it exists
  for real -- Plan never stages configuration for a device that hasn't actually been paired yet.

## Open risks / tradeoffs

- **The plugin contract itself doesn't exist in code yet.** `get_tool(s)`/`get_prompt()`/
  `handle_llm_result()` and the naming-convention dispatcher (prefix-match on tool name, forward to
  `send_command(plugin_device_id, ...)`) are a prerequisite piece of infrastructure, not something
  any single plan type can build incidentally. Every plugin-dependent plan type (Serenity,
  Security, Animal protection, and Holidays if backed by a calendar plugin) -- and Plan itself, if
  plan types are implemented as the privileged plugin tier described above -- depends on this
  landing first.
- **`apply_plan` partial-failure UX** is undecided: default to itemized per-op status (matches
  `_compare_links_files`' precedent of precise, structured reporting over a vague pass/fail), but
  this needs real testing against how verbose customers actually want that readback to be.
- **New installation / Move remain only partially automatable** until non-INSTEON pairing exists --
  this should be surfaced honestly to the customer/support, not glossed over by the LLM.
- **No plugin install path** means Plan can detect and recommend but never execute a plugin
  install; every plan type that leans on a plugin (Serenity, Security, Animal protection) must
  degrade gracefully to "here's what you'd need to add" rather than assuming it can just do it.
- **Testing story**: Diagnostics is read-only and safe to test directly against a live system (see
  `--diagnostic-step`/`--diagnostic-params` CLI flags added for that purpose). Plan's write
  operations need an equivalent that doesn't risk real damage -- recommend `apply_plan` support a
  `dry_run` flag that runs the existence/role/link-type validation (`get_node_roles`/
  `get_link_types` already exist for this) without actually committing, so the same
  direct-CLI-testing pattern Diagnostics established can extend to Plan safely.
- **Scope not yet decided**: whether all 17 plan types ship at once or in some order is deferred --
  per this round's direction, all get designed and stubbed now; sequencing which ones get built out
  first is a separate decision.

## Status

Design only. No code, tools, or prompts have been written yet.
