# Plugin authoring Phase 4 — full scope: real scaffold generation, local install, OAuth, AI-tool wiring, hardware detection

> Shipped (Stages 0-6 below all implemented and tested on the `plugin_authoring` branch).
> Supersedes the earlier `generate_plugin_scaffold`-only draft that previously lived at this path
> — that draft assumed `plugin_py` is 100% free-text LLM output and that the prototype stays
> "generation-only, never installs/runs." Both are wrong, established by reading `ioxplugin`'s real
> templates/AST codegen, the actually-installed `udi_interface` package, `plugin-api.md`'s
> local-dev-store endpoint, and direct correction from the user. Read
> [`impl_plan.md`](impl_plan.md) first if you haven't; this document assumes its Phase 1-3
> vocabulary (`EvidenceLedger`, `path_confinement.confine_path`, the `plugin_output_root`/
> `location` convention) without re-explaining it.

## Status

All six stages are implemented and tested (`python -m pytest tests/unified/plugin_authoring
tests/unified/test_run_unified_runtime.py -q` — 131 passed; full suite — 963 passed, the same 9
pre-existing failures unrelated to this work, see this doc's git history/PR for detail). Stages 4
and 5's skeleton wiring (the `OAUTH`/`CUSTOMREQUEST` subscriptions, the OAuth-placeholder
enforcement, the AI-tool/helper matching guard) ended up implemented as part of Stage 2 itself,
since `plugin_skeleton.render_init`'s own signature (`authorize`/`ai_enabled`) already required
it — Stages 4/5 contributed their dedicated tests and the real-OAuth-endpoint-value guidance, not
separate code.

- **Stage 1** — `src/nucore/nucore_interface.py`, `src/iox/iox_wrapper.py`,
  `src/unified/handlers/plugin_management.py`, `src/unified/plugin_authoring/tools/
  tool_plugin_configure.json`.
- **Stage 2** — `src/unified/plugin_authoring/secret_guard.py` (new, extracted from
  `handlers/discovery.py`), `src/unified/plugin_authoring/plugin_skeleton.py` (new),
  `src/unified/plugin_authoring/handlers/scaffold.py` (new), `tools/
  tool_generate_plugin_scaffold.json` (new), `handlers/workspace.py` (context.md support),
  `dispatch.py`.
- **Stage 3** — `src/unified/plugin_authoring/handlers/install.py` (new), `tools/
  tool_install_generated_plugin.json` (new), `dispatch.py`.
- **Stage 4/5** — covered by Stage 2's own `authorize`/`ai_enabled` conditionals; dedicated tests
  in `tests/unified/plugin_authoring/test_scaffold.py`.
- **Stage 6** — `src/unified/plugin_authoring/handlers/device_detection.py` (new), `tools/
  tool_detect_usb_device.json` (new), `plugin_skeleton.render_devd_rule`, `run_unified_runtime.py`
  (`run_shell_command` added to `_PLUGIN_AUTHORING_REUSED_CUSTOMER_TOOLS`), `dispatch.py`.

## Context

**`ioxplugin` is fully retired, not referenced as a dependency or even as a structural template
to adapt.** It was "just a convenience package that converted json files to xml nodedefs (gone)
and generated python code (gone)." Phase 4 *replaces* that code-generation role entirely.
Everything salvaged from reading it (the real `subscribe()` event map, the override-method
contract, the OAuth mechanics) was independently re-verified against the actually-installed
`udi_interface` package and `plugin-api.md` — not trusted on `ioxplugin`'s say-so. One real bug
was caught doing this (`ioxplugin`'s own template never wires the `OAUTH` subscription —
commented out) and is not carried forward.

**The "generation-only" principle is reversed.** Confirmed with the user: Phase 4 actually
registers, installs, and starts the generated plugin locally — not just writes files for a human
to finish.

## Explored and confirmed

- **Real `udi_interface` v3.4.5 API** (checked by introspecting the actually-installed package,
  not assumed): `Node.__init__(self, poly, primary, address, name)`, `setDriver`, `reportCmd`;
  `Interface(classes, options=None)`, `start(version=None)`, `ready()`, `runForever()`,
  `subscribe(self, topic, callback, address=None)`, `addNode(self, node, conn_status=None,
  rename=False)`; constants `START/STOP/CONFIG/CONFIGDONE/CUSTOMPARAMS/POLL/ADDNODEDONE/
  DELNODEDONE/CUSTOMNS/CUSTOMDATA/DISCOVER/BONJOUR/OAUTH` all real on `Interface`.
  `CUSTOMREQUEST`/`customResponse`/`customResponseError` are **not** present in this pinned
  version but are confirmed real/required by the user for AI-enabled plugins — treat as a
  version-mismatch in this repo's pinned dependency, not a reason to avoid using them; generate
  plugin code against the contract the user gave, not the locally-pinned version's gaps.
- **`udi_interface.OAuth(self, polyglot)` is real** and independent of `ioxplugin.OAuthService`
  (which wrapped it and is gone). It's a pure config/token *client*: config arrives via
  `CUSTOMNS` (`key='oauth'`) with fields `auth_endpoint`/`token_endpoint`/`client_id`/
  `client_secret`/`addRedirect`/`addScope`/`scope`/`token_parameters`; fresh tokens arrive via the
  separate `OAUTH` topic; `getAccessToken()` auto-refreshes. `ioxplugin`'s own template
  subscribes `CUSTOMNS` but leaves `OAUTH` commented out — a real bug, not reproduced here.
- **`plugin-api.md`'s `PUT /api/plugins/store/local/entry`** is the authoritative, complete
  developer/tool-defined parameter set for a plugin:
  `{name (max 15 chars), type: 'python3'|'node', path, executable, runAs, desc?, nsdata?,
  oauth?, customParams?, devd?, aiPrompt?, aiTools?, isyAccess?, discover?, authorize?,
  fileUpload?, shortPoll?, longPoll?, nsInfoPoll?}`.
  Per the user: `customParams` is the *schema* (field names/descriptions the developer/tool
  defines — e.g. `api_key`); actual *values* are filled in later by the customer via the
  already-shipped `configure_plugin` tool. `shortPoll`/`longPoll` are their own dedicated fields,
  not part of `customParams`, though customers can update them later too (`POST
  /api/plugin/:profileNum/config`). `nsdata` is hardly used — deferred. `devd` is a `devd.conf`-
  style hardware-attach rule for serial/USB devices (`UDX_OWNER_PLACE_HOLDER`/
  `UDX_PERMISSION_PLACE_HOLDER` substituted by the platform at runtime); building one requires
  detecting the device's vendor/product id by asking the customer to plug/unplug it, via the
  shell tool.
  `POST /api/plugins/store/local/install` (`{nsid, profileNum?}` → `{profileNum}`) installs from
  this local entry; `aiPrompt`/`aiTools` are what `GET /api/plugin/:profileNum/prompt`/
  `.../tools` return — static, developer-authored metadata, not a live negotiation.
- **No `NuCoreInterface`/`IoXWrapper` method exists yet for either endpoint** — `install_plugin`'s
  only real implementation is the marketplace URL-handback stub (`plugin_management.py:138`),
  unrelated to local dev-store registration. This has to be built fresh.
- **`IoXWrapper.configure_plugin` (`iox_wrapper.py:1970`) is unimplemented** —
  `raise NotImplementedError(...)`, even though the already-shipped `configure_plugin` tool calls
  it. `put()`/`get()`/`post()`/`delete()` primitives all exist and work (confirmed real at
  `iox_wrapper.py:492-566`); `plugin_ops` (`iox_wrapper.py:1948`) is fully real and maps directly
  to `/api/plugin/:profileNum/{start,stop,restart}` — reusable as-is for starting a newly-installed
  plugin. Per `plugin-api.md`, the real custom-param-*values* endpoint is
  `POST /api/plugin/:profileNum/custom/customparams`, not `/config` (`/config` is `{shortPoll,
  longPoll, allowIsyAccess}` only) — `configure_plugin`'s real implementation should target
  `/custom/customparams`.
- **`run_shell_command`** (`src/unified/handlers/shell.py`) is a real, shipped customer-tool-set
  tool — arbitrary shell exec as whatever account this process runs under, no content allowlist,
  bounded output/timeout. Not currently in `plugin_authoring`'s reused-customer-tools list; needed
  for Stage 6's device detection.
- **The AI-tool-calling handler contract**, given directly by the user as the canonical pattern
  (method name `handle_custom_request`, snake_case — not `ioxplugin`'s dead
  `__handleCustomRequest`): extract `requestId`/`payload`/`tool_name`, dispatch by `tool_name` to
  one async helper per declared tool, `customResponseError` on missing/unknown-tool/empty-result,
  `customResponse(requestId, {'result': json.dumps(result, default=str)})` on success.
- **The real, simplified main-file job**, once `ioxplugin`'s own build-time pieces
  (`Plugin(...)`, `.toIoX()`, `.generateCode()`) are stripped from `iox_main_template.py`:
  `polyglot = udi_interface.Interface([]); polyglot.start(version.ud_plugin_version); <import+
  construct the Controller>; polyglot.ready(); polyglot.runForever()`. Needs a small `version.py`
  alongside it.
- Phase 3's `path_confinement.confine_path`/`EvidenceLedger.has_evidence()`/the `context.md`
  mechanism from the prior draft are unaffected and still apply.

## Design decisions

- **`plugin.py` is a hybrid, not 100% free LLM text**: a deterministic skeleton (tool-generated
  `__init__` + private wiring methods — the exact verified `subscribe()` calls, conditionally
  including `CUSTOMNS`/`OAUTH`/`CUSTOMREQUEST` wiring per the spec's `authorize`/`aiTools` flags)
  with LLM-authored override-method **bodies only** (`start`/`stop`/`discover`/`shortPoll`/
  `longPoll`/`processConfig`/`parameterHandler`/`customParamHandler`/per-declared-tool async
  helpers) spliced in. This is the same shape `ioxplugin`'s own template used (fixed boilerplate +
  hand-authored override half) — reusing that *shape*, never its package or a generic
  AST-templating engine, and only for the parts now independently verified against the real API.
  This replaces the earlier draft's "plugin_py is one opaque LLM-authored string."
- **`main.py`/`version.py` are fully tool-generated, not LLM-authored** — their content is nearly
  identical for every plugin (bootstrap + one import + `runForever`); no reason to risk LLM
  variance on pure boilerplate.
- **A new 5th generated artifact**: `server_entry.json`, the exact `PUT .../store/local/entry`
  body. `oauth`/`aiPrompt`/`aiTools`/`devd` keys are present only when the spec actually calls for
  them (not-OAuth/not-AI-enabled/not-hardware plugins omit them entirely, matching the API's
  `?`-optional fields).
- **OAuth secrets are placeholders at generation time, always** — `client_id`/`client_secret` in
  `server_entry.json`'s `oauth` field are never real values (impl_plan.md's existing
  secret-placeholder principle). Real values get filled in post-install the same way
  custom-param values do.
- **`configure_plugin`'s real fix is in scope here** (Stage 1) because Stage 3/4 directly depend
  on it actually working, not because this plan is auditing the whole codebase for bugs.
- **devd is generated only when the spec says the device needs direct serial/USB access** — most
  API/cloud-service-backed plugins (the common case this whole prototype targets) never touch it.
- **`run_shell_command` is reused into `plugin_authoring`'s dispatch** (like the four
  plugin-lifecycle tools already are), not reimplemented.

## Stages

### Stage 1 — Backend plumbing (new capability + one bug fix)
- `NuCoreInterface` (abstract) + `IoXWrapper` (concrete): two new methods —
  `register_local_plugin(entry: dict) -> Any` → `PUT /api/plugins/store/local/entry` (via
  existing `self.put`);
  `install_local_plugin(nsid: str, profile_num: int | None = None) -> Any` →
  `POST /api/plugins/store/local/install` (via existing `self.post`), returns `{"profileNum": int}`.
- Fix `IoXWrapper.configure_plugin`: replace `NotImplementedError` with a real call to
  `POST /api/plugin/{plugin_id}/custom/{key}`, body = `config` (the values dict). Add
  `key: str = "customparams"` as a new parameter (on both the `NuCoreInterface` abstract signature
  and the `IoXWrapper`/`plugin_management.configure_plugin`/`tool_plugin_configure.json` call
  chain) so the same method also serves Stage 4's `key="oauth"` case — one key-generic method, not
  two. Matches the `put`/`post` conventions every other real method here already uses (try/except,
  `None`/non-200 → `None`, log on error).
- Tests: mocked HTTP for both new methods and the `configure_plugin` fix; confirm the exact
  path/body shape per `plugin-api.md`.

### Stage 2 — Full artifact generation (rewrite `generate_plugin_scaffold`)
- **`src/unified/plugin_authoring/plugin_skeleton.py`** (new): builds the deterministic half.
  - `render_init(*, node_classes: list[str], authorize: bool, ai_enabled: bool) -> str`: the exact
    verified `subscribe()` list (`START`→`__start`, `CUSTOMPARAMS`→`parameterHandler`, `POLL`→
    `__poll`, `STOP`→`__stop`, `CONFIG`→`__configHandler`, `CONFIGDONE`→`__configDoneHandler`,
    `ADDNODEDONE`/`DELNODEDONE`/`CUSTOMNS`/`CUSTOMDATA`/`DISCOVER`/`BONJOUR`), plus `CUSTOMNS`'s
    routing to `oauthService.customNsHandler` and the `OAUTH` subscription (fixing `ioxplugin`'s
    dead-code bug) when `authorize`, plus `CUSTOMREQUEST`→`handle_custom_request` when
    `ai_enabled`.
  - `render_private_wiring_methods(...) -> str`: `__start`/`__stop`/`__poll`/`__configHandler`/
    `__configDoneHandler`/`__addNodeDoneHandler`/`__removeNodeDoneHandler`/`__customNSHandler`/
    `__updateStatus`/`__getStatus` — the regenerated half, unchanged across plugins.
  - `render_main_py(controller_module, controller_class) -> str` /
    `render_version_py(version) -> str`.
  - `render_init` also always sets `self.data_dir` (next to the plugin's own files, created via
    `os.makedirs(..., exist_ok=True)`) -- any override body that needs to persist something
    beyond `customParams`/`customData` (a local cache, session tokens, etc.) writes it there, not
    loose in the plugin's own directory.
- **`handlers/scaffold.py`**: `generate_plugin_scaffold`'s input becomes structured: `location`,
  `profile` (unchanged), `override_bodies: dict[method_name, code]` (LLM-authored, spliced into
  the skeleton — only `start`/`stop`/`discover`/`shortPoll`/`longPoll`/`processConfig`/
  `parameterHandler`/`customParamHandler`/declared AI-tool helper names are accepted; an unknown
  key is rejected), `node_classes` (additional LLM-authored device Node subclasses beyond the
  Controller, if the profile has more than one NodeDef), `server_entry` (the structured spec for
  `server_entry.json` — `name`/`desc`/`customParams` schema/`authorize`/`oauth` placeholders/
  `ai_enabled`/`aiPrompt`/`aiTools`/`discover`/`fileUpload`/`isyAccess`/`shortPoll`/`longPoll`/
  `devd`), `readme_body`, `tests`, `iteration_note`.
- Guard sequence (extends the prior draft's six guards): `ast.parse` now runs on the
  **assembled** `plugin.py` (skeleton + spliced bodies + any extra node-class files), not a
  single opaque string; secret-guard scans every LLM-authored piece *and* `server_entry`
  (catching an accidentally-real `client_secret`); `server_entry.oauth.client_id`/`client_secret`,
  if present, must equal the literal placeholder constant this tool defines, never anything
  else — a non-placeholder value is refused outright, not just logged.
- Files written on success: `profile.json`, `plugin.py` (assembled), any extra node-class `.py`
  files, `main.py`, `version.py`, `server_entry.json`, `README.md` (+ Sources), each
  `tests/test_*.py`, `context.md` iteration entry — same append-only/no-conflict rule as the
  prior draft. A `data/` subdirectory is also always created (empty, outside the overwrite-conflict
  check, same as `context.md`) -- what `self.data_dir` points to at runtime.

### Stage 3 — Real registration, install, and start
- New tool `install_generated_plugin(location)`: reads `server_entry.json` (via the
  Stage-2-written file, confined the same way `read_generated_plugin` already is), derives `nsid`
  as `f"local.{location}"`, calls `register_local_plugin(entry)` → `install_local_plugin(nsid)` →
  `list_installed_plugins` (resolve the real `plugin_id`/`profileNum` the install returned) →
  `plugin_ops(plugin_id, "start")`. Each step's failure is reported distinctly (`{"stage":
  "register"|"install"|"start", "error": ...}`) — no silent retry, no partial success reported as
  full success.
- Deliberately a separate tool from `generate_plugin_scaffold`, not a fused step — lets the model
  (and the customer) review the generated artifacts before anything touches the real hub, and lets
  `install_generated_plugin` be re-called alone if only the install/start step failed.

### Stage 4 — OAuth wiring
- `plugin_skeleton.render_init`/`render_private_wiring_methods` include the OAuth subscriptions
  and `self.oauthService = udi_interface.OAuth(self.poly)` when `server_entry.authorize` is true.
- `server_entry.oauth`'s `auth_endpoint`/`token_endpoint`/`scope`/`addRedirect`/`addScope`/
  `token_parameters` come from the Phase-2 evidence (the target API's real documented OAuth
  endpoints) — `client_id`/`client_secret` are always the fixed placeholder string, per the
  design decision above.
- The real values reach the running plugin via the `oauth` `CUSTOMNS` key, not `customparams`.
  `configure_plugin` (both the `IoXWrapper` method from Stage 1 and the tool spec) gains an
  optional `key` argument, defaulting to `"customparams"`, accepting `"oauth"` too — one
  key-generic method targeting `POST /api/plugin/:profileNum/custom/:key`, matching
  `plugin-api.md`'s own key-generic endpoint, rather than a second dedicated tool.

### Stage 5 — AI-tool wiring
- `plugin_skeleton` includes the `CUSTOMREQUEST` subscription and a `handle_custom_request` method
  matching the user-supplied pattern exactly (dispatch by `tool_name`, `customResponseError` for
  missing/unknown/empty, `customResponse` with `json.dumps(result, default=str)` on success)
  whenever `server_entry.ai_enabled`/`aiTools` is non-empty.
- `server_entry.aiPrompt`/`aiTools` and each dispatch branch's async helper (in `override_bodies`)
  are generated together — one `aiTools` entry without a matching helper (or vice versa) is a
  guard failure, not a silent gap.
- Expected to be the exception, not the default — most generated plugins (ordinary device/API
  integrations) have no `aiTools` at all and skip this entirely.

### Stage 6 — `devd` / hardware detection
- `run_shell_command` added to `plugin_authoring`'s reused-customer-tools list in `dispatch.py`/
  `run_unified_runtime.py` (same mechanism as the four plugin-lifecycle tools already reused).
- New tool `detect_usb_device`: prompts a plug/unplug comparison (run a device-enumeration
  command, ask the customer to plug the device in, run it again, diff), extracts vendor/product
  id from the result, and returns them — the actual shell commands run through
  `run_shell_command`, this tool is a thin, conversational wrapper describing the two-snapshot
  workflow, not a new shell-exec primitive.
- `generate_plugin_scaffold` accepts a `devd` field on `server_entry` (vendor id, product id, a
  stable name) and renders the `attach { match ...; action "chown UDX_OWNER_PLACE_HOLDER ..."; ...
  }` rule text from the fixed template, substituting only those three values — the
  `UDX_*_PLACE_HOLDER` tokens stay literal (platform-substituted at runtime, never filled in by
  this tool).
- Only invoked when the spec indicates direct hardware/serial access is needed.

## Tests (per stage, extending the prior draft's `test_scaffold.py`/`test_dispatch.py`/`test_workspace.py`)

- Stage 1: new `IoXWrapper` methods + `configure_plugin` fix, mocked HTTP, exact path/body
  assertions.
- Stage 2: skeleton rendering is deterministic/stable for a fixed input; assembled `plugin.py`
  round-trips through `ast.parse`; an unknown `override_bodies` key is rejected; `server_entry.json`'s
  shape matches `plugin-api.md`'s body exactly; a non-placeholder `client_secret` is refused.
- Stage 3: each of register/install/start failing independently reports the right `stage` field;
  full success resolves the real `plugin_id` via `list_installed_plugins`.
- Stage 4: `OAUTH` subscription is present when `authorize=true` and absent otherwise (closing
  `ioxplugin`'s bug, not reproducing it); placeholder enforcement.
- Stage 5: `CUSTOMREQUEST`/`handle_custom_request` present only when AI-enabled; every `aiTools`
  entry has a matching dispatch branch and helper.
- Stage 6: devd rule text matches the fixed template with only vendor/product/name substituted;
  `UDX_*_PLACE_HOLDER` tokens never altered.

## Verification

- `python -m pytest tests/unified/plugin_authoring tests/unified/test_run_unified_runtime.py -q`
  after each stage.
- Full suite re-run after Stage 1 (backend changes touch shared `IoXWrapper` code) — confirm only
  the pre-existing, unrelated 9 failures remain.
- Manual, end-to-end, Stage 1-3 only (Stages 4-6 are conditional paths, exercise once their own
  guards are in place): generate a real small plugin, call `install_generated_plugin`, confirm
  `list_installed_plugins` shows it and `plugin_ops`-reported state is running on a real test hub.
