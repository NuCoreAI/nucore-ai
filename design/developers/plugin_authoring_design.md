# Plugin authoring — design decisions and implementation history

> Living history doc: research → design → what actually shipped, for the `plugin_authoring`
> tool set and the dev-tooling research that led to it. Consolidated from `plugin_dev_tooling.md`,
> `ai_plugin_authoring_pipeline.md`, `impl_plan.md`, and `plugin_authoring_p4_impl.md` (originals
> archived under `legacy/`). Where an earlier doc's design was superseded by what actually
> shipped, this says so explicitly rather than presenting the superseded version as current.

See also: [Domain and plugin models](domain_and_plugin_models.md) · [APIs](plugin_apis.md) ·
[Lifecycle and runtime](plugin_lifecycle_and_runtime.md)

## Current status

**Shipped and tested** (full suite green on the `plugin_authoring` branch): the merged
developer-testing + customer-facing-authoring tool set (`src/unified/plugin_authoring/`,
renamed in place from `unified.dev_tools`), covering all of Phases 1-7 below plus everything in
Stages 1-6 of the Phase 4 scope — discovery (store/GitHub/web search, with native Claude search
support), workspace recall, full scaffold generation (profile/code/tests/README/
`server_entry.json`, OAuth placeholders, AI-tool wiring, a `data/` directory for anything a
generated plugin needs to persist), real local install (register → install → start), and
USB/serial hardware detection. Since Phase 4's own "Shipped" note was written, the system prompt
and both `plugin_authoring` READMEs were rewritten to cover all 17 tools and the merged
developer+customer audience (they previously only documented 7 developer tools and claimed no
install path existed); `validate_profile` gained a UOM-table cross-check that catches an
*existing*-but-wrong UOM paired with the wrong range shape, not just a missing one; and the
generated plugin's bootstrap was fixed to actually call `polyglot.updateJsonProfile()` on
startup — before that fix, every generated plugin (including the first real one produced)
installed and started with no profile registered with the hub at all, since writing
`profile.json` to disk never did anything by itself (see
[Domain and plugin models](domain_and_plugin_models.md) §6).

## 1. Developer tooling research (`plugin_dev_tooling.md`)

**Ecosystem research**, done live (GitHub, UDI's own wiki/PyPI/VS Code Marketplace), since the
plugin-developer experience lives entirely outside this repo. Found the official Python starter
template (`udi-poly-template-python`), the `udi_interface` PyPI package (whose `API.md`
documents the Dynamic Profiles API), several community plugins, and UDI's own practical wiki
pages (node-server tutorial, testing/debugging, packaging for distribution).

**`ioxplugin` + `iox-vscode-plugin`** — both real, actively maintained, both this project's own
prior tooling (same author). `ioxplugin` (Python, PyPI) generates the **classic static PG3
profile tree** (`nodedef.xml`/`editor.xml`/`nls`) plus AST-generated Python stub files from a
`*.iox_plugin.json` input — it never touches the Dynamic Profiles JSON format at all.
`iox-vscode-plugin` is a VS Code extension wrapping `ioxplugin`'s codegen with schema-backed
JSON authoring and a real breakpoint/step-through debug loop (confirmed by the team; not
independently line-verified).

**Decision: both frozen, not extended.** The static-XML format they generate is itself
runtime-obsolete — IoX regenerates any static profile files a plugin ships into Dynamic Profiles
and removes the static files, so static-XML generation is a bootstrapping shim the platform
already routes around. A new, Dynamic-Profiles-focused core was built instead, reviewed against
both tools' source and *selectively* carrying forward only the JSON Schemas (the single
highest-value asset — chiefly `iox-vscode-plugin`'s `uom.schema.json`, a ready-made UDI
UOM table, and its general editor-shape pattern) — restructured to validate the Dynamic Profiles
shape, not copied as-is. Explicitly dropped: the `ast`/`astor` code-generation engine (an LLM
generates Python stub code directly — no AST templating needed) and the static-XML/NLS emission
layer entirely.

**Difficulty assessment**: moderate-to-high, but not because the programming model is hard (a
simple subclass pattern, roughly a day's work to understand). The real friction is
environmental — no testing without a physical hub exists anywhere, official or community;
real-world plugins still hand-author/generate static XML despite the better JSON API existing;
and a scaffolding tool does exist, it just targets the older format.

**Recommendation** (confirmed, sequencing below): (1) build the new Dynamic-Profiles-focused
core, carrying forward only the restructured JSON Schemas — **done**, see
[Domain and plugin models](domain_and_plugin_models.md) §12; (2) build a web/AI-assisted flow in
eisy-ai on top of it — the primary near-term target, since this persona (non-technical, building
a local plugin for their own eisy, which is the hub eisy-ai already runs on) has no
personal-hardware gap to solve — **this is what `plugin_authoring` is**; (3) a CLI for the new
core, sequenced after (2), confirmed in scope but not yet built; (4) a VS Code front-end for the
new core, explicitly deferred, not scoped; (5) a personal-hardware-gap fix (simulator, or
remote/shared real-hub access, Claude-Code-Remote-Control-style) for CLI/IDE-based development —
runs independently, doesn't block (1)-(3), not yet designed.

## 2. The early pipeline design (`ai_plugin_authoring_pipeline.md`) — what carried forward, what didn't

This was the original end-to-end design for "Front-end 3" above: describe a device, get a
working plugin, fully automated through install → pytest simulation → live validation against
the real hub, with three named terminal failure modes (**E1** no usable public API, **E2**
pytest never passes after 3 fix attempts, **E3** live validation never passes after 3 fix
attempts).

**What it correctly identified as already available**: the `AgenticLoop` tool-calling
infrastructure, `validate_profile`/`lookup_uom`, `configure_plugin`/`plugin_ops`, the
ordinary device command-dispatch path (no plugin special-casing), and eisy-ai's own hub-side
access meaning no simulator gap for this persona specifically. **The one real blocker it found**:
no automatable local-install path existed (`install_plugin` was, and still is for the
*marketplace* flow, a URL-handback stub) — it recommended starting with a human-in-the-loop
install (hand the user a link, poll for completion) rather than blocking everything on building
real local-store automation first.

**What actually shipped is simpler than this design, by deliberate choice — stated plainly so
nothing here is mistaken for describing the current system:**
- **No retry-budget orchestrator/state-machine was built.** The pipeline's Stage 5-6 envisioned
  a driver sitting above the chat loop tracking attempt counters across an automated
  generate→test→fix→retry→live-validate loop (capped at 3 attempts per stage). What shipped
  instead is two discrete, conversational tools — `generate_plugin_scaffold` and
  `install_generated_plugin` — called explicitly, turn by turn, with the human (model + customer)
  as the retry loop. There is no Stage 5 (automated pytest-against-mocked-external-API
  generation and retry) and no Stage 6 (automated live-IoX command/response validation with
  retry) in the shipped tool set at all.
- **Local install was resolved via option (a), not the recommended (b)**: `install_generated_plugin`
  really does register → install → start a plugin directly (Phase 4, Stage 3 below) — the
  human-in-the-loop fallback this doc recommended starting with was superseded once Phase 4
  built real local-store automation.
- **Still genuinely open today** (survived from this doc's §4, independent of the
  never-built orchestrator): the **blast radius of generated code** — this whole feature's
  premise is LLM-generated code calling an arbitrary third-party API, executed on the user's
  real eisy under the same elevated access any marketplace plugin gets, and nothing here grants
  it more, but nothing sandboxes it further either; and **cleanup on terminal failure** — a
  failed or abandoned generation can leave a plugin installed-but-never-started, and neither
  `install_generated_plugin` nor anything else automatically uninstalls it (`delete_plugin` is
  still a URL-returning stub for the marketplace flow, not a direct action usable here either).
  Both remain unaddressed design gaps, not resolved by anything that shipped.

## 3. The prototype implementation plan (`impl_plan.md`) — Phases 1-7

**Problem and approach.** Renamed the existing developer-facing `unified.dev_tools` tool set to
`unified.plugin_authoring` in place (a merge, not a new parallel package) and extended it to also
serve a non-technical customer: focused intake questions, check the NuCore plugin store first and
recommend a viable existing plugin rather than generating a duplicate, and — only if the customer
explicitly wants a custom plugin — seek credible implementation evidence through a bounded
fallback chain. Never invents undocumented API behavior; creates no files when no credible source
is available. For a viable custom request, generates a scaffold (Dynamic Profiles JSON, Python
backend, pytest tests, README) using the wire-format shape, not the nested catalog shape. The
prototype never installs/starts/executes/live-tests generated code **without being asked** —
installing is a separate, explicitly-confirmed tool call (Phase 4, Stage 3), not an automatic
step after generation.

**Decisions confirmed with the user**:
- Output goes to a customer-selected directory restricted to a configured allowed root
  (`--plugin-output-root`).
- A viable store match is recommended first and the flow stops; a custom plugin is generated only
  if the customer asks to continue.
- Fallback order after no viable store match: (1) GitHub search — callable any time, not a
  required first step; (2) web search — Claude's own native `web_search` when the resolved
  provider is Claude (no second API key), otherwise a configurable engine (`--search-engine
  brave|tavily`, key via `SEARCH_ENGINE_API_KEY`, env-var-only, never in `runtime_config`) — a
  GitHub result surfacing through *either* path gets the same license check; (3) ask the customer
  for URLs; (4) if nothing credible turns up, report why and create no files.
- Public plugin source is a baseline only after license/quality/compatibility vetting; preserve
  notices, never copy incompatible or unlicensed code.
- Existing output files: warn and require explicit confirmation before overwriting.
- Never put customer credentials/secrets in search queries, generated source, prompts, or logs;
  generated code uses configuration placeholders.

**Wiring**: a tool set is plain constructor injection into `UnifiedRuntime` — nothing registers
it. `_resolve_tool_set(tool_set, nucore_interface)` returns `(tool_spec_paths, dispatch,
system_prompt_builder)`; `customer` returns `(None, None, None)` and gets the runtime's built-in
defaults, so every `plugin_authoring`-only change is additive and verified unreachable from the
customer path. The dispatch is a **per-connection factory**, not a shared closure — each
connection/session gets its own fresh `EvidenceLedger`, so one customer's evidence can never
satisfy another's "credible source" check.

**Phases** (all shipped; see "Current status" above for what's been added since):
1. **Skeleton and runtime wiring** — the `dev_tools`→`plugin_authoring` rename in place,
   `--plugin-output-root`, the per-connection dispatch factory.
2. **Discovery tools** — `search_store_plugins`, `search_github_plugins`, `search_web`/native
   Claude search, `fetch_reference`, the `EvidenceLedger`. The original "GitHub must be tried
   before web search" sequencing rule was later **removed** (a Claude-native web search call is
   a server-side tool the dispatch layer never sees invoked, so that rule couldn't be enforced
   once native search existed) and replaced with uniform domain-based classification: any
   `github.com` URL surfacing from *either* path gets the same license-vetting treatment,
   regardless of which tool found it.
3. **Workspace discovery** — `list_generated_plugins`/`read_generated_plugin`, local-disk-only,
   always registered (no key/engine gating), so a returning customer's prior work is
   discoverable.
4. **Scaffold generation** — see Phase 4 full scope below; this phase's original draft (profile
   validation bridging the wire/catalog shapes, output confinement, overwrite-consent flow) was
   superseded by a substantially larger rewrite once real local install/OAuth/AI-tool-wiring/
   hardware-detection requirements were confirmed.
5. **Prompt** — non-technical-user framing: focused intake, store-first recommend-and-stop, the
   fallback ladder with no fabricated API behavior, no secrets in queries or code,
   generation-only unless install is explicitly confirmed. Later rewritten in full (see Phase 4
   section and "Current status") once the tool set's actual scope outgrew the original 7-tool
   prompt.
6. **Tests** (`tests/unified/plugin_authoring/`, network mocked throughout).
7. **Documentation** — `plugin_authoring/README.md` and the top-level README's tool-set section,
   kept in sync with the actual shipped tool list (also later rewritten in full, see "Current
   status").

**Research and safety notes**: bound automated research (caps per tier), stop once evidence is
sufficient, report source links and vetting rationale; no writes outside the allowed root, keep
output in a dedicated directory, confirm before replacing; generated Python and external
repositories are untrusted — not imported or executed during discovery or generation, no contact
with external device APIs; generated profiles must be the wire format, tested as such explicitly.

## 4. Phase 4 full scope (`plugin_authoring_p4_impl.md`) — real scaffold generation, local install, OAuth, AI-tool wiring, hardware detection

This phase **reversed** the original prototype's "generation-only" principle (confirmed with the
user): the tool set actually registers, installs, and starts a generated plugin locally, not
just writes files for a human to finish. It also fully retires `ioxplugin` as even a structural
reference — everything salvaged from reading it (the real `subscribe()` event map, the
override-method contract, the OAuth mechanics) was independently re-verified against the
actually-installed `udi_interface` package and [the Plugin API](plugin_apis.md), not trusted on `ioxplugin`'s
say-so. One real bug was caught this way and *not* carried forward: `ioxplugin`'s own template
never wires the `OAUTH` subscription (left commented out).

**Explored and confirmed against the real, installed `udi_interface` v3.4.5** (introspected, not
assumed): the real `Node`/`Interface` method surface (`setDriver`, `reportCmd`, `start`, `ready`,
`runForever`, `subscribe`, `addNode`), and the real event constants
(`START/STOP/CONFIG/CONFIGDONE/CUSTOMPARAMS/POLL/ADDNODEDONE/DELNODEDONE/CUSTOMNS/CUSTOMDATA/
DISCOVER/BONJOUR/OAUTH`). `CUSTOMREQUEST`/`customResponse`/`customResponseError` aren't present
in this pinned version but are confirmed real/required for AI-enabled plugins — generated against
the contract given, not the locally-pinned version's gap. `udi_interface.OAuth(polyglot)` is a
real, independent config/token client: config arrives via `CUSTOMNS` (`key='oauth'`;
`auth_endpoint`/`token_endpoint`/`client_id`/`client_secret`/`addRedirect`/`addScope`/`scope`/
`token_parameters`), fresh tokens via the separate `OAUTH` topic, `getAccessToken()`
auto-refreshes.

`PUT /api/plugins/store/local/entry` is the authoritative local-dev-plugin parameter set (see
[APIs](plugin_apis.md)). `customParams` is the config *schema* the plugin author defines; actual
*values* are filled in later by the customer via `configure_plugin`. `shortPoll`/`longPoll` are
dedicated fields, updatable later via `POST /api/plugin/:profileNum/config`. `devd` is a
`devd.conf`-style hardware-attach rule (`UDX_OWNER_PLACE_HOLDER`/`UDX_PERMISSION_PLACE_HOLDER`
substituted by the platform at runtime). Neither `NuCoreInterface` nor `IoXWrapper` had a method
for local-store registration/install before this phase — built fresh.
`IoXWrapper.configure_plugin` was an unimplemented stub despite the tool already calling it, and
the real custom-param-*values* endpoint turned out to be `POST
/api/plugin/:profileNum/custom/customparams`, not `/config` (`/config` is
`{shortPoll,longPoll,allowIsyAccess}` only) — fixed as part of this phase since Stages 3-4
directly depend on it working, not as an unrelated audit finding.

**Design decisions**: `plugin.py` is a **hybrid**, not 100% free LLM text — a deterministic
skeleton (tool-generated `__init__` + private wiring methods, the exact verified `subscribe()`
calls, conditionally including `CUSTOMNS`/`OAUTH`/`CUSTOMREQUEST` wiring per the spec's
`authorize`/`aiTools` flags) with LLM-authored override-method **bodies only** spliced in — the
same shape `ioxplugin`'s own template used, reusing that shape, never its package or a generic
AST-templating engine. `main.py`/`version.py` are fully tool-generated (bootstrap boilerplate is
nearly identical for every plugin — no reason to risk LLM variance there).
`server_entry.json` is a 5th generated artifact, the exact `PUT .../store/local/entry` body —
`oauth`/`aiPrompt`/`aiTools`/`devd` keys present only when the spec actually calls for them.
OAuth secrets are **always** placeholders at generation time (never real values in
`server_entry.json`'s `oauth` field — real values arrive post-install via `configure_plugin
(key="oauth")`), the same principle as every other secret-handling rule in this tool set.
`devd` is generated only when the spec says the device needs direct serial/USB access — most
API/cloud-service-backed plugins never touch it. `run_shell_command` is reused into
`plugin_authoring`'s dispatch (like the four plugin-lifecycle tools already are), needed for
Stage 6's device detection.

**Stages, all shipped and tested**:
1. **Backend plumbing** — `register_local_plugin`/`install_local_plugin` on `NuCoreInterface`/
   `IoXWrapper`; the `configure_plugin` fix above, generalized to accept a `key` parameter
   (defaulting to `"customparams"`, also serving Stage 4's `key="oauth"` case).
2. **Full artifact generation** — `plugin_skeleton.py` (deterministic half:
   `render_init`/`render_private_wiring_methods`/`render_main_py`/`render_version_py`,
   plus always setting `self.data_dir` and, as of this session, sending the profile via
   `updateJsonProfile()` on every startup — see "Current status"); `handlers/scaffold.py`'s
   `generate_plugin_scaffold`, with a six-guard sequence (location confinement; evidence
   required; profile validation, reusing `validate_profile` exactly; override-method/AI-tool-name
   allowlist; `ast.parse` round-trip on the *assembled* `plugin.py`, confirming every expected
   override actually lands inside the class, not beside it; secret-guard scan of every
   LLM-authored piece plus `server_entry`, with placeholder enforcement for OAuth secrets) and an
   overwrite flow (conflict list on the first call, proceed only with `confirm_overwrite: true`;
   `context.md` is the one exception, append-only, never part of the conflict check).
3. **Real registration, install, and start** — `install_generated_plugin`: reads
   `server_entry.json` → `register_local_plugin` → `install_local_plugin` → resolves the real
   `plugin_id` via `list_installed_plugins` → `plugin_ops(start)`. Deliberately a separate tool
   from generation, not a fused step — lets the model/customer review generated artifacts before
   anything touches the real hub, and lets this tool alone be re-called if only install/start
   failed.
4. **OAuth wiring** — covered by Stage 2's own `authorize` conditional; no separate code.
5. **AI-tool wiring** — covered by Stage 2's own `ai_enabled` conditional
   (`CUSTOMREQUEST`/`handle_custom_request`, dispatch by `tool_name`,
   `customResponseError`/`customResponse` matching the canonical pattern); every declared AI tool
   must have a matching dispatch helper and vice versa — one without the other is a guard
   failure, not a silent gap. Expected to be the exception, not the default, for ordinary
   device/API-integration plugins.
6. **`devd`/hardware detection** — `detect_usb_device`: a thin diffing utility over two
   shell-command snapshots (before/after the customer plugs the device in), extracting
   vendor/product id for `server_entry.devd`. Never runs a shell command itself —
   `run_shell_command` does that.
