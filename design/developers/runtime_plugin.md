# The plugin framework — a developer reference

> Reference doc, not a proposal. It explains what's shipped today. For proposed extensions,
> see the two "Where this is headed" links at the bottom -- don't design against this doc alone.

## 1. Two senses of "plugin" (read this first)

The codebase overloads the word "plugin." Keep them separate:

- **Marketplace plugins** (what the rest of this doc covers): third-party ISY/Polyglot
  node-servers -- e.g. YouTube, Airscape, HusqvarnaMower, Sun, Camect -- that nucore-ai
  *manages and calls* purely over REST. They are never imported as Python code; there is no
  `Plugin` base class they implement in this repo. This is the "plugin marketplace" feature:
  list/install/buy/delete/configure/start/stop/restart a plugin, and call the tools a plugin
  itself declares.

- **nucore-ai itself as a plugin**: nucore-ai is *deployed as* a Polyglot/pg3 node-server on
  the ISY/eisy host, the same way the marketplace plugins above are deployed. It uses the
  third-party `udi_interface` SDK for this (`src/iox/iox_wrapper.py:422-431`, importing
  `udi_interface`/`unload_interface`/`LOGGER` and receiving a `poly` interface instance -- see
  also `design/shell-tool.md:43-52` for the deployment-account details). This sense is
  unrelated to the marketplace-plugin feature below; don't conflate the two.

## 2. The host-side abstraction

`src/nucore/nucore_interface.py:484-642` -- `class NuCoreInterface(ABC)` declares the plugin
methods any backend must implement:

- `get_active_plugins()` -- store listing (`GET /api/plugins/store/prod/list/active`)
- `get_purchased_plugins()` -- purchased licenses, keyed by `nsid` only; pair with
  `get_active_plugins()` to resolve a human-readable name (`GET /api/plugins/licenses`)
- `get_installed_plugins()` -- plugins installed on this device (`GET /api/plugins`); response
  rows carry `profileNum`, which becomes `plugin_id` for every subsequent call
- `_get_plugin_number(plugin_id)` / `_get_plugin_nsid(nsid)` -- id-resolution guards (see below)
- `plugin_ops(plugin_id, operation: Literal["start", "stop", "restart"])`
- `configure_plugin(plugin_id, config)`
- `get_plugin_prompt(plugin_id)` -- fetches the plugin's own natural-language usage guidance
- `get_plugin_tools(plugin_id)` -- fetches the plugin's own declared tool specs
- `handle_plugin_llm_result(plugin_id, args)` -- forwards an LLM tool call to the plugin

**Id-guessing guard pattern** (`_get_plugin_number`/`_get_plugin_nsid`,
`nucore_interface.py:526-586`): the LLM is sometimes handed a plugin's display name (e.g.
"Sun") instead of the real `profileNum`/`nsid`, or invents a slugified id. Both resolvers treat
their input as untrusted -- they try to parse a real id first (`n008_x`, `plugin_N`, a digit
string, or a UUID for nsid), and if that fails, fall back to *re-querying* the real listing
(`get_installed_plugins()` / `get_active_plugins()`) and matching by name. The model's input is
never trusted outright; it's always verified against a live listing.

## 3. The only concrete implementation

`class IoXWrapper(NuCoreInterface)` at `src/iox/iox_wrapper.py:367` implements the plugin
methods at `iox_wrapper.py:1893-2074` as thin REST wrappers over the ISY's `/api/plugin(s)/...`
endpoints. The full endpoint surface (44 rows -- install/config/custom-records/notices/oauth/
prompt/tools/request/start/stop/restart, store listing/licenses/purchase/PayPal) is mapped in
`design/iox_apis/plugins-api.csv`; `IoXWrapper` is a partial client of it.

## 4. LLM-facing tool layer

Same three-layer stack as every other tool in this codebase (see `design/shell-tool.md` for the
pattern write-up): JSON schema in `tools/`, async handler in `handlers/`, hand-maintained
dispatch entry.

| Layer | Location |
|---|---|
| Schemas | `src/unified/tools/tool_plugin_{install,buy,delete,get_capabilities,list_installed,list_purchased,list_store,ops,call}.json` (9 files) |
| Handler | `src/unified/handlers/plugin_management.py` |
| Dispatch wiring | `src/unified/dispatch.py:17,45-53` |

**Dangerous-action pattern**: `install_plugin`, `buy_plugin`, and `delete_plugin` never act
directly -- they hand back a URL for a human to finish the action. This is the one existing
precedent `design/shell-tool.md:24-25` contrasts its own (direct-execution) design against.
Follow it for any new plugin action that's similarly irreversible or purchase-related.

## 5. Runtime call flow

1. `list_installed_plugins` / `list_purchased_plugins` / `list_store_plugins`
   (`plugin_management.py:76,99,55`) query `IoXWrapper`.
2. `get_plugin_capabilities` (`plugin_management.py:214-233`) lazily fetches a specific
   plugin's `get_plugin_prompt` + `get_plugin_tools`. Tool names come back uniquified as
   `f"{plugin_id}_{tool_name}"` (`iox_wrapper.py:2031-2034`) so multiple plugins' tools can
   coexist in the same LLM context without colliding.
3. `call_plugin` (`plugin_management.py:236-255`) strips the `{plugin_id}_` prefix back off,
   then POSTs `{tool_name, ...args}` to `/api/plugin/{plugin_id}/request` via
   `handle_plugin_llm_result` (`iox_wrapper.py:2048-2074`, 60s timeout).

**Current limitation, by design, not a bug**: plugin calls are stateless, single-shot tool
calls. A plugin cannot ask a follow-up question, call another tool, or persist state across
calls today. See section 8 for the proposal that addresses this.

## 6. Discovery / registration mechanics

Two distinct mechanisms -- don't conflate them:

- **Marketplace plugins**: fully dynamic, resolved at conversation time entirely over HTTP.
  Nothing about a specific plugin (YouTube, Sun, ...) is registered anywhere in this repo's
  Python code.
- **This app's own tool schemas**: discovered by filesystem glob at process startup --
  `src/unified/runtime.py:47`, `_TOOLS_DIR.glob("tool_*.json")` where `_TOOLS_DIR =
  Path(__file__).parent / "tools"`. Dropping a new `tool_plugin_*.json` file is picked up
  automatically as an LLM-callable schema; no code change needed for the schema itself.
- **Handler wiring**: static and hand-maintained. `TOOL_HANDLERS: dict[str, ToolHandler]` in
  `src/unified/dispatch.py:31-64` maps each tool name (the 9 plugin tools included, lines
  45-53) to its handler function. There is no auto-registration/entry-point/decorator system --
  every new tool needs a manual dict entry here, plus a matching `NuCoreInterface` abstract
  method if it's backend-facing.

## 7. Tests

- `tests/unified/handlers/test_plugin_management.py` -- 36 tests, exercising every handler in
  `plugin_management.py` end-to-end through `execute_tool()` against a `FakeBackend
  (NuCoreInterface)` stub. Covers listing + name-joining, install/buy/delete link generation,
  id/nsid guess-resolution regressions (e.g. the model passing "Sun" instead of `profileNum
  6`), `get_plugin_capabilities`, and `call_plugin`'s prefix-stripping.
- `tests/iox/test_plugin_endpoints.py` -- 4 tests, verifying `IoXWrapper.get_installed_plugins()`
  and `plugin_ops()` hit the right REST endpoints, plus failure-mode handling (connection error,
  non-200).

Use these as the pattern to extend when adding a new plugin-management tool or backend method.

## 8. Where this is headed (pointers only -- not designed here)

- [`design/future-consideration/agentic-plugins-future-consideration.md`](../future-consideration/agentic-plugins-future-consideration.md)
  -- analysis of what it would take to evolve today's stateless single-call plugin invocation
  into a true multi-step agent (multi-turn resume, richer context, task delegation, cross-call
  state, a trust/authorization gate). Not built.
- [`design/future-consideration/plan-design-future-consideration.md`](../future-consideration/plan-design-future-consideration.md#L83-L150)
  -- describes a plugin-side contract (`get_tool(s)()` / `get_prompt()` / `handle_llm_result()`)
  that a plugin's own backend exposes. **Correction**: this file is headed "FOR FUTURE
  CONSIDERATION ONLY -- NOT IN USE," but the contract it describes is now recognizable as
  already shipped, under different names -- `IoXWrapper.get_plugin_prompt`/`get_plugin_tools`/
  `handle_plugin_llm_result` (`iox_wrapper.py:1982-2074`), called from the LLM-tool side via
  `get_plugin_capabilities`/`call_plugin` (section 5 above). See
  [`plugin_concepts_and_lifecycle.md`](plugin_concepts_and_lifecycle.md) §1 "AI-capable
  plugins" and §3 Stage 5b for the verified, working mechanism, with a real example (a
  Hebrew-calendar/"hebcal" plugin). What's still genuinely unbuilt is only the trust-model
  reasoning for why first-party "Plan" types would get privileged write access while
  third-party plugins stay sandboxed to their own declared API, and the multi-turn/stateful
  extension the previous bullet describes -- not the basic single-shot AI-plugin call itself.
