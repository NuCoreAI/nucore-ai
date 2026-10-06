# Plugin lifecycle and runtime — the full picture, develop to call

> Reference doc, not a proposal. It explains what's shipped today. For proposed extensions, see
> §8's "Where this is headed" — don't design against this doc alone. Consolidated from
> `runtime_plugin.md` and §2/§3/§6-8 of `plugin_concepts_and_lifecycle.md` (originals archived
> under `legacy/`).

See also: [Design decisions and implementations](plugin_authoring_design.md) ·
[Domain and plugin models](domain_and_plugin_models.md) · [APIs](plugin_apis.md)

## 1. Two senses of "plugin" (read this first)

The codebase overloads the word "plugin." Keep them separate:

- **Marketplace plugins** (what the rest of this doc covers): third-party ISY/Polyglot
  node-servers — e.g. YouTube, Airscape, HusqvarnaMower, Sun, Camect — that nucore-ai *manages
  and calls* purely over REST. They are never imported as Python code; there is no `Plugin` base
  class they implement in this repo.
- **nucore-ai itself as a plugin**: nucore-ai is *deployed as* a Polyglot/pg3 node-server on the
  ISY/eisy host, the same way the marketplace plugins above are deployed, using the third-party
  `udi_interface` SDK (`src/iox/iox_wrapper.py:422-431` — see also `design/shell-tool.md:43-52`
  for deployment-account details). Unrelated to the marketplace-plugin feature below.

## 2. The full lifecycle, stage by stage

Stages 0-2 happen outside nucore-ai; stages 3-6 happen inside it. The "lifecycle" is not any one
of the model/API docs by itself — it's the full develop → publish → install → run → call
pipeline, and no single source covers all of it.

**Stage 0 — Developer lifecycle (partially documented).** Before a plugin is installable by
anyone: (1) **develop** — write the plugin's backend and its Dynamic Profile (see
[Domain and plugin models](domain_and_plugin_models.md) §6-9); (2) **test locally** — publish to
a local plugin store on the developer's own machine; (3) **publish to the production store** —
the plugin becomes installable by customers. Steps 2-3 each warrant their own future document;
neither exists today. See [Design decisions and implementations](plugin_authoring_design.md) for
`plugin_authoring`'s own conversational take on generating and locally installing a plugin,
which is a different (LLM-assisted, same-hub) path through step 2, not a general answer to it.

**Stage 1 — Customer-side lifecycle (marketplace).** Once published, a customer manages an
installed plugin through six actions: **install** (may require payment), **configure**,
**start**, **stop**, **restart**, **delete**.

The host-side abstraction every backend must implement
(`src/nucore/nucore_interface.py:484-642`, `class NuCoreInterface(ABC)`):
- `get_active_plugins()` / `get_purchased_plugins()` / `get_installed_plugins()` — store and
  installed-plugin listings; an installed row's `profileNum` becomes `plugin_id` for every
  subsequent call.
- `_get_plugin_number(plugin_id)` / `_get_plugin_nsid(nsid)` — **id-guessing guards**: the LLM is
  sometimes handed a plugin's display name (e.g. "Sun") instead of the real `profileNum`/`nsid`,
  or invents a slugified id. Both resolvers try to parse a real id first, and on failure fall
  back to *re-querying* the real listing and matching by name — the model's input is never
  trusted outright, always verified against a live listing.
- `plugin_ops(plugin_id, operation)`, `configure_plugin(plugin_id, config)`,
  `get_plugin_prompt(plugin_id)`, `get_plugin_tools(plugin_id)`,
  `handle_plugin_llm_result(plugin_id, args)` (Stage 5b).

The only concrete implementation: `class IoXWrapper(NuCoreInterface)`
(`src/iox/iox_wrapper.py:367`), thin REST wrappers over `/api/plugin(s)/...`
(`iox_wrapper.py:1893-2074`) — a **partial client** of the full 44-row endpoint surface
[Plugin APIs](plugin_apis.md) documents (also mapped in `design/iox_apis/plugins-api.csv`).

**LLM-facing tool layer** — same three-layer stack as every tool in this codebase (schema in
`tools/`, async handler in `handlers/`, hand-maintained dispatch entry; see
`design/shell-tool.md` for the pattern write-up):

| Layer | Location |
|---|---|
| Schemas | `src/unified/tools/tool_plugin_{install,buy,delete,get_capabilities,list_installed,list_purchased,list_store,ops,call}.json` (9 files) |
| Handler | `src/unified/handlers/plugin_management.py` |
| Dispatch wiring | `src/unified/dispatch.py:17,45-53` |

**Dangerous-action pattern**: `install_plugin`, `buy_plugin`, and `delete_plugin` never act
directly — they hand back a URL for a human to finish. Follow this for any new plugin action
that's similarly irreversible or purchase-related; `configure_plugin`/`plugin_ops` (start/stop/
restart) are the precedent for actions that *do* act directly.

**Discovery/registration mechanics** — two distinct, don't conflate them: marketplace plugins
are fully dynamic, resolved at conversation time entirely over HTTP, nothing about a specific
plugin registered anywhere in this repo's Python code; this app's **own** tool schemas are
discovered by filesystem glob at process startup (`_TOOLS_DIR.glob("tool_*.json")`) — dropping a
new `tool_plugin_*.json` is picked up automatically, no code change needed for the schema
itself, but **handler wiring is static and hand-maintained** (`TOOL_HANDLERS` dict in
`dispatch.py`) — every new tool needs a manual dict entry plus a matching `NuCoreInterface`
abstract method if it's backend-facing.

**Stage 2 — Plugin defines its device model.** The plugin's own backend process starts and sends
its `NodeDef`/`Editor`/`LinkDef` set to PG3/IoX via `updateJsonProfile` (or ships static profile
files — same eventual effect from IoX's point of view). See
[Domain and plugin models](domain_and_plugin_models.md) §6-9 for the full mechanics and the
step-by-step authoring recipe. This is entirely between the plugin and PG3/IoX; nucore-ai is not
involved and does not see this JSON directly.

**Stage 3 — IoX ingests the profile into its catalog.** PG3/IoX folds the submitted
`{nodedefs, editors, linkdefs}` into the hub's overall profile catalog under `family = "10"`
(`DEVICE_FAMILY_PLUGIN`, confirmed at `src/iox/iox_definitions.py:78,86`) and the **instance
slot** the platform assigned this plugin at install time. This assignment is done by the
NuCore/IoX platform itself, not by the plugin and not by nucore-ai's Python code — see
[Domain and plugin models](domain_and_plugin_models.md) §5 for the full explanation.

**Stage 4 — nucore-ai loads the catalog and resolves live nodes.** On (re)connect, the eager,
one-shot load sequence: `IoXWrapper.__load_profile__` (loads the profile, file or
`/rest/profiles`) → `__load_nodes__` (`/rest/nodes` XML) → `__load_groups_links__`
(`/api/groups/links`) → `_load_devices`, which calls `self.profile.map_nodes(root, glinks_root)`
and assigns the results into `self.runtime_profiles`/`self.nodes`/`self.groups`/`self.folders`.
Nothing here re-fetches per-node on demand; a full reload re-runs the same sequence.
`NuCoreInterface.__init__` seeds an empty `Profile` placeholder before any load. For a
plugin-originated device, this resolves its `Node.node_def` via `"{node_def_id}.10.{instance}"`
against the catalog built in Stage 3 — **the same generic family-indexed machinery used for
INSTEON/Z-Wave/Zigbee/Matter devices** — a plugin device is not a special case in this code
path.

**Stage 5a — Ordinary device commands (the common path).** Once resolved, a plugin-originated
`Node` behaves exactly like a native-protocol node for everyday control: the LLM picks a
property/command by name, `resolve_property_id`/`resolve_command_id` do a strict exact-match
lookup against that node's own `node_def`, `resolve_value` converts the customer's value against
the matched `Editor`, and the result is sent to the standard
`.../rest/nodes/{device_id}/cmd/{command_id}` endpoint — the same endpoint used for
INSTEON/Z-Wave devices. Nothing about this path is plugin-specific.

**Stage 5b — AI-capable plugin path (a separate, parallel mechanism, proven at runtime today).**
A plugin can declare a `prompt` and `tools` in its deployment manifest, separately from any
NodeDefs it submits — doing so marks it **AI-capable**, discovered by nucore-ai at runtime via
`get_plugin_capabilities`, not visible in the Dynamic Profiles JSON itself. This goes through a
**completely different** surface that never touches `Node`/`NodeDef`/`Command` at all, documented
directly in `src/unified/prompt/system_prompt.md`'s "PLUGIN WORKFLOW" section:

- **Trigger** (quoted verbatim): *"When no existing tool can satisfy what the customer's asking
  for, check whether a plugin can"* — this is prompt-level LLM guidance, not Python branching;
  there is no device→plugin fallback logic in code anywhere, the LLM decides via the agentic
  tool-calling loop because the system prompt tells it to.
- **Tool sequence**: `list_installed_plugins` → `get_plugin_capabilities(plugin_id)` (fetches the
  plugin's own `get_plugin_prompt`/`get_plugin_tools` over `/api/plugin/{id}/prompt`+`/tools`,
  tool names uniquified as `{plugin_id}_{tool_name}` so multiple plugins' tools can coexist in
  the same LLM context without colliding) → `call_plugin(plugin_id, tool_name, args)` (strips the
  prefix, POSTs to `/api/plugin/{plugin_id}/request`, 60s timeout).
- **The fetched `prompt` is "usage guidance," not a literal system-prompt swap** — it comes back
  as an ordinary tool-result string; nothing in code re-routes it into a system-prompt slot. The
  LLM reads it in context and follows it at its own discretion — enforced by instruction-
  following, not by code.
- **Installing a plugin the customer needs is consent-gated, not proactive** — `install_plugin`/
  `buy_plugin` are available, but only after the customer has explicitly agreed ("never
  speculatively"). There is no LLM-facing `configure` action for this flow — `configure_plugin`
  exists only as a backend method, with no `tool_plugin_configure.json` schema for the customer
  tool set (`plugin_authoring` reuses it directly for its own developer-testing flow — see
  [Design decisions and implementations](plugin_authoring_design.md)).
- **Result**: used to answer the customer, or to build a scene/automation from.

**Worked example, already running in production**: a Hebrew-calendar ("hebcal") plugin — baked
directly into the LLM's own instructions: *"A plugin may already compute or resolve exactly the
information you'd otherwise ask for (e.g. a Hebrew-calendar plugin deriving a Hebrew yahrtzeit
date from a Gregorian one, instead of asking the customer whether they happen to know the Hebrew
date themselves)."* The customer asks something a device can't answer, the LLM finds no existing
tool covers it, checks installed plugins, finds hebcal is AI-capable, fetches its prompt/tools,
calls it, and uses the result — before asking the customer any clarifying question.

Today this path is still stateless and single-shot at the protocol level: a single plugin tool
call cannot itself ask a follow-up question, call another tool, or persist state across calls
within that one call. See §8 for the parked proposal to extend this.

## 3. Tests

- `tests/unified/handlers/test_plugin_management.py` — 36 tests, exercising every handler in
  `plugin_management.py` end-to-end through `execute_tool()` against a `FakeBackend
  (NuCoreInterface)` stub. Covers listing + name-joining, install/buy/delete link generation,
  id/nsid guess-resolution regressions (e.g. the model passing "Sun" instead of `profileNum 6`),
  `get_plugin_capabilities`, and `call_plugin`'s prefix-stripping.
- `tests/iox/test_plugin_endpoints.py` — 4 tests, verifying `IoXWrapper.get_installed_plugins()`
  and `plugin_ops()` hit the right REST endpoints, plus failure-mode handling.

Use these as the pattern to extend when adding a new plugin-management tool or backend method.

## 4. Where this is headed (pointers only — not designed here)

- [`design/future-consideration/agentic-plugins-future-consideration.md`](../future-consideration/agentic-plugins-future-consideration.md)
  — analysis of what it would take to evolve today's stateless single-call plugin invocation
  into a true multi-step agent (multi-turn resume, richer context, task delegation, cross-call
  state, a trust/authorization gate). Not built.
- [`design/future-consideration/plan-design-future-consideration.md`](../future-consideration/plan-design-future-consideration.md#L83-L150)
  describes a plugin-side contract (`get_tool(s)()`/`get_prompt()`/`handle_llm_result()`) that a
  plugin's own backend exposes. That file is headed "FOR FUTURE CONSIDERATION ONLY — NOT IN
  USE," but the contract it describes is already shipped, under different names —
  `IoXWrapper.get_plugin_prompt`/`get_plugin_tools`/`handle_plugin_llm_result`, called from the
  LLM-tool side via `get_plugin_capabilities`/`call_plugin` (§2 Stage 5b above). What's still
  genuinely unbuilt is only the trust-model reasoning for why first-party "Plan" types would get
  privileged write access while third-party plugins stay sandboxed to their own declared API,
  and the multi-turn/stateful extension the previous bullet describes — not the basic
  single-shot AI-plugin call itself.
