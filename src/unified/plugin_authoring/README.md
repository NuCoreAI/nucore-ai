# Plugin Authoring

`unified.plugin_authoring` (formerly `unified.dev_tools` -- renamed in place, see
[`design/developers/impl_plan.md`](../../../design/developers/impl_plan.md)) is a tool set/system
prompt for the same agentic loop the customer-facing runtime uses (`nucore` +
`unified.loop.AgenticLoop`) -- just with a different `tools/`, `dispatch.py`, and prompt builder
wired in. **One tool set, two audiences, not two tool sets**: it serves a plugin *developer*
testing/debugging an already-installed plugin by hand, and a non-technical *customer* who wants a
plugin for some device/service and is guided through finding or generating one -- both through the
same natural-language chat loop instead of one-off scripts or a wizard UI. See
[`prompt/system_prompt.md`](prompt/system_prompt.md) for exactly how the model is told to tell
these two apart and behave in each.

Distinct from [`design/developers/`](../../../design/developers/), which documents the plugin
*architecture* itself (domain model, Dynamic Profiles spec, lifecycle) -- this package is the
runnable tool set built on top of that architecture.

## Running it

Selected via `run_unified_runtime.py`'s `--tool-set plugin_authoring` flag (default is `customer`).
Still requires a live backend via `--backend-api-classpath`, same as the customer tool set -- the
plugin-lifecycle tools it reuses (`configure_plugin`, `plugin_ops`, etc.) talk to a real hub. The
customer-facing discovery/generation tools below additionally require `--plugin-output-root`
(the tool set refuses to start without it).

```shell
python -m unified.run_unified_runtime \
  --runtime-config src/unified/runtime_config.example.json \
  --backend-api-classpath iox.IoXWrapper \
  --backend-api-base-url https://192.168.6.134 \
  --backend-api-username admin \
  --backend-api-password yourpassword \
  --tool-set plugin_authoring \
  --plugin-output-root ~/plugin-projects \
  --query "I want a plugin that exposes my pool controller"
```

Omit `--query` for an interactive REPL. Every other flag (`--secrets-file`, `--log-*`,
`--websocket-*`, etc.) works exactly as documented in the top-level `README.md` -- `--tool-set`
only changes which tools/prompt the loop uses, not how the process is launched.

## Discovery and generation workflow

For the customer-facing half (see `prompt/system_prompt.md` for the authoritative version the
model is actually given): check the NuCore store first and stop there if it already has a viable
match; otherwise research via GitHub search and/or web search (either order, either omitted) with
every GitHub-domain result license-checked the same way regardless of which tool surfaced it;
fall back to asking the customer for URLs only once web search has been tried or wasn't
available; generate a scaffold only once real evidence exists; confirm with the customer before
overwriting existing files or before installing anything on the real hub. `generate_plugin_scaffold`
and `install_generated_plugin` are deliberately separate tools -- generation only ever writes
local files, nothing touches the hub until install is called and confirmed. Any persistent data a
generated plugin needs beyond `customParams`/`customData` belongs under its own `persist/`
subdirectory (`self.persist_dir`, always created) -- never loose elsewhere in the plugin's
directory. `data/` (`self.data_dir`) is a separate, narrower directory created only when
`server_entry.fileUpload` is true -- it's what the host's File Manager API/UI operate on, not a
general persistence location.

### Native Claude web search vs. the Brave/Tavily fallback

`search_web` (Brave/Tavily) is registered only when `--search-engine {brave,tavily}` is passed
explicitly (with `SEARCH_ENGINE_API_KEY` set) -- passing it always forces that fallback, on any
LLM provider. Otherwise, when the resolved provider for this tool set is Claude, Claude's own
native `web_search` server tool is used instead automatically: no second API key, no `search_web`
tool offered to the model at all, invoked by Claude itself mid-response. For any other provider
with no engine configured, there is no web search capability at all and the flow falls through to
asking the customer for URLs. See `run_unified_runtime.py`'s `--search-engine` help text for the
exact precedence.

## Layout

| File/dir | Purpose |
|---|---|
| `dispatch.py` | Tool name -> handler dispatch table (`execute_tool`) and the per-connection `build_tool_handlers` factory (binds a fresh `EvidenceLedger` and server-side config to each connection's discovery/generation tools), same shape/contract as `unified.dispatch`. |
| `evidence_ledger.py` | Per-connection/session bookkeeping: what's been tried, what was found, whether `generate_plugin_scaffold`/the ask-for-URLs step are allowed yet. One fresh instance per connection -- never shared. |
| `search_result_enrichment.py` | Shared GitHub-domain classifier: any URL from `search_web`, native web search, or a user-supplied link gets the same follow-up license lookup `search_github_plugins` gets for free from its own search API, and is recorded onto the ledger identically regardless of which tool found it. |
| `secret_guard.py` | Shared "does this text contain a configured secret value" check, used by the discovery tools (before an outbound call) and `generate_plugin_scaffold` (before a write) to refuse rather than silently redact. Also defines the fixed OAuth placeholder string. |
| `path_confinement.py` | Shared "resolve and require this path stays under the allowed root" check used by `generate_plugin_scaffold`, `read_generated_plugin`, and `install_generated_plugin` -- rejects traversal, absolute escapes, and symlinked parents/targets. |
| `plugin_skeleton.py` | Renders the deterministic, regenerated-every-time half of a generated plugin's `plugin.py` (the `udi_interface` wiring methods, `main.py`, `version.py`) that `handlers/scaffold.py` splices LLM-authored override bodies into -- including always setting `self.persist_dir` (and `self.data_dir` too, only when `fileUpload` is true), for anything an override body needs to persist beyond `customParams`/`customData`. |
| `handlers/profile_authoring.py` | `validate_profile`/`lookup_uom`/`lookup_property_id` -- local, hub-free authoring aids. |
| `handlers/discovery.py` | `search_store_plugins`, `search_github_plugins`, `search_web`, `fetch_reference` -- the customer-facing research tools. |
| `handlers/workspace.py` | `list_generated_plugins`/`read_generated_plugin` -- local-disk-only, always registered (no key/engine gating), so a returning customer's prior work is discoverable. `read_generated_plugin` returns every generated file (profile, plugin.py/main.py/version.py, server_entry.json, install.sh, requirements.txt, generation_inputs.json, tests, README, LICENSE.md, context.md, sources.md) by default, or just a requested subset via its `files` argument. |
| `handlers/developer_config.py` | `get_developer_config` (read-only -- the commissioned `{email, name, github_url?, default_run_as?}` on file for this `plugin_output_root`, or `configured: false`) and `configure_developer` (one-time-per-workspace commissioning write, covering every plugin generated under it -- not a per-plugin step; callers should check `get_developer_config` first and only call this reactively). |
| `handlers/scaffold.py` | `generate_plugin_scaffold` -- the real artifact generator (profile, plugin code, tests, README, `server_entry.json`, an empty `persist/` directory and, only when `fileUpload` is true, an empty `data/` directory), with its guard sequence (evidence required, profile validation, override-method allowlist, `ast.parse` round-trip, secret scan, overwrite confirmation). |
| `handlers/dev_venv.py` | `setup_dev_venv` -- local/dev-testing only: creates a per-plugin `.venv` and installs `requirements.txt` into it, for running the plugin's own `tests/` or for `install.sh`'s own `.venv` check to find before install. Idempotent -- a no-op if a working `.venv` already exists, unless `force` is passed. |
| `handlers/boilerplate.py` | `regenerate_plugin_boilerplate` -- picks up a template/tooling fix on an already-generated plugin by re-rendering only `plugin.py`/`main.py`/`version.py`/`install.sh` with today's template code, reading back `generation_inputs.json` (written by `generate_plugin_scaffold`) instead of the caller re-supplying everything. Never touches `server_entry.json`/`profile.json`/README/tests/node-class files. Plugins generated before this existed have no `generation_inputs.json` and need one `generate_plugin_scaffold` call first. |
| `handlers/install.py` | `install_generated_plugin` -- register -> install -> start on the real hub, each stage's failure reported distinctly. Persists the host-assigned `nsid` back into `server_entry.json` after a successful registration, and checks it against the host's local-store/installed-plugins lists on every later call, refusing with a `"conflict"` response rather than silently re-registering/re-installing. `update_registered_plugin`/`delete_registered_plugin` resolve a reported conflict's registered half. |
| `handlers/vscode_debug.py` | `setup_vscode_debug_config` -- local/dev-testing only: after `install_generated_plugin`, copies the plugin's real `PG3INIT` value from its `/usr/local/etc/rc.d/plugin_<profileNum>` script into a local `.iox_env`, and writes a `.vscode/launch.json` pointing at it, so a developer can attach a local debugger with the same identity the real daemonized process uses. Always overwrites both files -- `PG3INIT` rotates on every real restart. |
| `handlers/device_detection.py` | `detect_usb_device` -- diffs two shell-command snapshots to extract a USB device's vendor/product id. |
| `prompt/prompt_builder.py`, `prompt/system_prompt.md` | Builds the plugin_authoring system prompt. Deliberately not `unified.prompt_builder` -- that one assembles customer-specific `DEVICE DATABASE`/`ROUTINES DATABASE`/preference sections that don't belong here. |
| `tools/` | One `tool_<name>.json` spec per tool authored in this package, auto-discovered via a `tool_*.json` glob (minus `tool_search_web.json` when no engine/key is configured) plus the reused plugin-lifecycle specs (see `run_unified_runtime.py`'s `_resolve_tool_set`). |

## Tools

Developer-testing tools (reused directly from `unified.handlers.plugin_management`/`unified.handlers.shell`,
not reimplemented, so the tool sets never drift on what they do):

| Tool | Description |
|---|---|
| `list_installed_plugins` | Lists plugins installed on the device -- name, `plugin_id`, `isLocal`. Resolves `plugin_id` for every other plugin tool. |
| `configure_plugin` | Sets/changes an installed plugin's own custom-config parameters (API keys, polling interval, device settings, ...); also how a generated plugin's real OAuth `client_id`/`client_secret` are set post-install (`key="oauth"`), since the generated `server_entry.json` only ever contains a placeholder there. |
| `plugin_ops` | Starts/stops/restarts an installed plugin's own service. |
| `get_plugin_capabilities` / `call_plugin` | Inspect and exercise the tools an AI-capable plugin itself declares. |
| `run_shell_command` | Direct shell access, reused for `detect_usb_device`'s device-enumeration commands (and general diagnostics). |
| `uninstall_installed_plugin` | Frees an installed plugin's slot entirely (unlike `plugin_ops`'s "stop", which keeps it) -- resolves `install_generated_plugin`'s "already installed" conflict. Not in the customer tool set, same boundary as `delete_plugin` (a real delete stays developer-only), but unlike `delete_plugin` this one actually deletes. |

Customer-facing discovery/generation tools (authored in this package -- see `handlers/` above for
which module each lives in):

| Tool | Description |
|---|---|
| `search_store_plugins` | Checks the NuCore plugin store first, before any research or generation; scores candidates against the customer's own words and flags a viable match to recommend-and-stop on. |
| `search_github_plugins` | Public GitHub repo search for an existing implementation or a device's own API client -- a fallback tier, callable in any order relative to `search_web`. Flags any non-permissively-licensed result as reference-only. |
| `search_web` | Brave/Tavily general web search for official API docs -- registered only when `--search-engine`+key are configured; otherwise Claude's own native `web_search` may be active instead (see below), transparent to this tool list. |
| `fetch_reference` | Fetches one URL (from either search tier, or user-supplied once web search has been tried/was unavailable) as untrusted reference text. |
| `list_generated_plugins` | Lists everything already generated under `--plugin-output-root` for this installation, most-recently-worked-on first -- always registered, local-disk-only. |
| `read_generated_plugin` | Loads one previously-generated plugin's files back into context before discussing/modifying it -- every file by default, or just a requested subset via `files`. |
| `generate_plugin_scaffold` | Writes a complete local plugin (profile, code, tests, README, `server_entry.json`) once real evidence has been gathered; refuses and writes nothing otherwise, or on an unconfirmed overwrite. |
| `setup_dev_venv` | Local/dev-testing only: creates a per-plugin `.venv` and installs `requirements.txt` into it. Call before running the plugin's own `tests/` or before `install_generated_plugin`. Idempotent -- a no-op if a working `.venv` already exists, unless `force` is passed. |
| `install_generated_plugin` | Registers, installs, and starts an already-generated plugin on the real hub -- the one tool in this flow that isn't purely local; always confirm with the customer first. Refuses with a `"conflict"` response if this plugin's `nsid` is already registered and/or installed, rather than silently duplicating/re-installing. |
| `update_registered_plugin` | Resolves a reported conflict: pushes the current `server_entry.json` to the host in place (update, not create); also syncs to the installed record automatically if installed. |
| `delete_registered_plugin` | Resolves a reported conflict the other way: deletes the registration entirely and clears the locally-known `nsid`, so the next `install_generated_plugin` call registers fresh. |
| `setup_vscode_debug_config` | Local/dev-testing only: call after `install_generated_plugin`. Copies the real `PG3INIT` value from `/usr/local/etc/rc.d/plugin_<profileNum>` into a local `.iox_env`, and writes a `.vscode/launch.json` pointing at it, for attaching a local VS Code/debugpy debugger with the same identity the real process uses. Always overwrites both files. |
| `regenerate_plugin_boilerplate` | Local/dev-testing only: re-renders `plugin.py`/`main.py`/`version.py`/`install.sh` with today's template code, reusing the `override_bodies`/`authorize`/`ai_enabled`/etc. recorded in `generation_inputs.json` -- how an already-generated plugin picks up a template/tooling fix without a full regenerate. Requires `generation_inputs.json` (plugins generated before this tool existed need one `generate_plugin_scaffold` call first). Never touches `server_entry.json`/`profile.json`/README/tests/node-class files. |
| `detect_usb_device` | Diffs a before/after shell-command snapshot pair to extract a plugged-in device's vendor/product id, for plugins needing direct hardware access. |

Developer-authoring tools (authored in this package):

| Tool | Description |
|---|---|
| `validate_profile` | Parses a Dynamic Profiles JSON document with `nucore`'s own profile loader and reports structural problems, including every property id's format and every editor range's UOM consistency. Purely local. See `src/nucore/schemas/README.md` for the JSON Schema equivalent of the shape this validates. |
| `lookup_uom` | Searches the Unit of Measure catalogue by keyword. Purely local. |
| `lookup_property_id` | Searches the standard NodeDef Property id catalogue (`ST`, `CLITEMP`, `CLIHUM`, ...) by keyword -- use before inventing a custom property id. Purely local. |

Deliberately excluded: `buy_plugin`/`delete_plugin`/`list_store_plugins`/`list_purchased_plugins`
(marketplace-only concerns) and the customer tool set's own `install_plugin` (a purchase-flow stub
that hands back a URL rather than installing anything -- `generate_plugin_scaffold` +
`install_generated_plugin` are the real local-dev generate/install path).

## Tests

`tests/unified/plugin_authoring/` -- one file per module above
(`test_dispatch.py`, `test_evidence_ledger.py`, `test_search_result_enrichment.py`,
`test_path_confinement.py`, `test_profile_authoring.py`, `test_discovery.py`, `test_workspace.py`,
`test_scaffold.py`, `test_install.py`, `test_device_detection.py`), network mocked throughout.
