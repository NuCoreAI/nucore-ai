# Dev Tools

`unified.dev_tools` is a second, plugin-*developer*-facing tool set/system prompt for the same
agentic loop the customer-facing runtime uses (`nucore` + `unified.loop.AgenticLoop`) -- just with
a different `tools/`, `dispatch.py`, and prompt builder wired in. It helps someone authoring an
IoX/Polyglot plugin validate a Dynamic Profiles JSON document, look up UOM ids, and configure/
start/stop/call an already-installed plugin under test, all through the same natural-language
chat loop instead of one-off scripts.

Distinct from [`design/developers/`](../../../design/developers/), which documents the plugin
*architecture* itself (domain model, Dynamic Profiles spec, lifecycle) -- this package is the
runnable tool set built on top of that architecture.

## Running it

Selected via `run_unified_runtime.py`'s `--tool-set dev_tools` flag (default is `customer`). Still
requires a live backend via `--backend-api-classpath`, same as the customer tool set -- the
plugin-lifecycle tools it reuses (`configure_plugin`, `plugin_ops`, etc.) talk to a real hub.

```shell
python -m unified.run_unified_runtime \
  --runtime-config src/unified/runtime_config.example.json \
  --backend-api-classpath iox.IoXWrapper \
  --backend-api-base-url https://192.168.6.134 \
  --backend-api-username admin \
  --backend-api-password yourpassword \
  --tool-set dev_tools \
  --query "Validate this profile: {...}"
```

Omit `--query` for an interactive REPL. Every other flag (`--secrets-file`, `--log-*`,
`--websocket-*`, etc.) works exactly as documented in the top-level `README.md` -- `--tool-set`
only changes which tools/prompt the loop uses, not how the process is launched.

## Layout

| File/dir | Purpose |
|---|---|
| `dispatch.py` | Tool name → handler dispatch table (`execute_tool`), same shape/contract as `unified.dispatch`. |
| `handlers/profile_authoring.py` | `validate_profile`/`lookup_uom` -- local, hub-free authoring aids. |
| `prompt/prompt_builder.py`, `prompt/system_prompt.md` | Builds the dev-tools system prompt. Deliberately not `unified.prompt_builder` -- that one assembles customer-specific `DEVICE DATABASE`/`ROUTINES DATABASE`/preference sections that don't belong here. |
| `tools/` | One `tool_<name>.json` spec per tool authored in this package (`tool_validate_profile.json`, `tool_lookup_uom.json`, `tool_plugin_configure.json`), auto-discovered via a `tool_*.json` glob plus the reused plugin-lifecycle specs (see `run_unified_runtime.py`'s `_resolve_tool_set`). |

## Tools

| Tool | Source | Description |
|---|---|---|
| `validate_profile` | `handlers/profile_authoring.py` | Parses a Dynamic Profiles JSON document with `nucore`'s own profile loader and reports structural problems (missing ids, dangling editor references, malformed properties/commands). Purely local -- never touches the hub. See `src/nucore/schemas/README.md` for the JSON Schema equivalent of the shape this tool parses (not wired into this tool -- for anyone hand-authoring/vetting documents outside the chat loop). |
| `lookup_uom` | `handlers/profile_authoring.py` | Searches the Unit of Measure catalogue (`nucore.uom.PREDEFINED_UOMS`) by keyword against name/label/description/category, so an editor's UOM id doesn't have to be guessed from memory. Purely local. |
| `configure_plugin` | reused from `unified.handlers.plugin_management` | Sets/changes an installed plugin's own custom-config parameters (API keys, polling interval, device settings, ...). Requires `plugin_id` from `list_installed_plugins`. |
| `list_installed_plugins` | reused from `unified.handlers.plugin_management` | Lists plugins installed on the device -- name, `plugin_id`, `isLocal`. Resolves `plugin_id` for every other plugin tool. |
| `plugin_ops` | reused from `unified.handlers.plugin_management` | Starts/stops/restarts an installed plugin's own service. |
| `get_plugin_capabilities` | reused from `unified.handlers.plugin_management` | Fetches a plugin's usage prompt and the tools it declares, so `call_plugin` knows what it can invoke. |
| `call_plugin` | reused from `unified.handlers.plugin_management` | Invokes one of a plugin's own declared tools (from `get_plugin_capabilities`). |

The four reused tools are shared directly (not reimplemented) with the customer tool set, so the
two never drift on what "start"/"stop"/"configure a plugin" actually does -- see `dispatch.py`'s
module docstring. Deliberately excluded: `buy_plugin`/`delete_plugin`/`list_store_plugins`/
`list_purchased_plugins` (marketplace-only concerns) and `install_plugin` (its handler is a
purchase-flow stub that returns a URL rather than installing anything -- not a real local-dev
install path; see `design/developers/plugin_dev_tooling.md` for the state of that gap).

## Tests

`tests/unified/dev_tools/` -- `test_dispatch.py` (dispatch table/unknown-tool/exception handling),
`test_profile_authoring.py` (`validate_profile`/`lookup_uom` behavior).
