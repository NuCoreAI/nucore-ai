# Conversational Plugin Authoring Prototype — Implementation Plan

## Problem and approach

This renames the existing developer-facing `unified.dev_tools` tool set (`--tool-set dev_tools`)
to `unified.plugin_authoring` (`--tool-set plugin_authoring`) in place, and extends it to also
serve a non-technical-customer-facing flow. **`dev_tools` and `plugin_authoring` are the same tool
set, not two** — this is a merge, not a new parallel package. The merged tool set keeps every tool
`dev_tools` already ships (`validate_profile`, `lookup_uom`, `configure_plugin`, `plugin_ops`,
`get_plugin_capabilities`, `call_plugin`, `list_installed_plugins` — including the live,
hub-connected device-control ones) and adds the following customer-facing capability: it asks
focused intake questions, checks the NuCore plugin store first, and recommends a viable existing
plugin rather than generating a duplicate. If the user explicitly wants a custom plugin, it seeks
credible implementation evidence through a bounded fallback chain. It must not invent undocumented
API behavior, and it creates no files when no credible source is available.

For a viable custom request it generates a small scaffold: a Dynamic Profiles JSON document, a Python backend, pytest tests, and a README. The profile document uses the Dynamic Profiles **wire payload** shape (top-level `editors`, `nodedefs`, `linkdefs`), not NuCore's internal catalog shape (`families[]` → `instances[]` → definitions).

The prototype is generation-only: it never installs, starts, executes, or live-tests generated code on the hub.

## Decisions confirmed with the user

- Deliver a scaffold: Dynamic Profile JSON, Python backend, pytest tests, README.
- Output goes to a user-selected directory restricted to a configured allowed root.
- A viable store match is recommended first and the flow stops; a custom plugin is generated only if the user asks to continue.
- Fallback order after no viable store match:
  1. GitHub search for eisy/IoX/NuCore plugin repositories, following references to official API
     documentation -- callable any time, not a required first step (see Phase 2).
  2. Web search -- Claude's own native `web_search` tool when the resolved provider is `claude`
     (no second API key), otherwise a configurable engine (`--search-engine brave|tavily`), key
     supplied through `SEARCH_ENGINE_API_KEY` (env-var-only, never in `runtime_config`). A GitHub
     result surfacing through either path gets the same license check `search_github_plugins`
     itself would have given it.
  3. Ask the user for URLs.
  4. If no credible source results, report why and create no files.
- Public plugin source is a baseline only after license, quality, and compatibility vetting; preserve notices, never copy incompatible or unlicensed code.
- Existing output files: warn and require explicit user confirmation before overwriting.
- Never put user credentials/secrets in search queries, generated source, prompts, or logs; generated code uses configuration placeholders.

## How it is instantiated and launched

### Wiring (existing pattern)

A tool set is plain constructor injection into `UnifiedRuntime`; nothing registers it.

1. `main()` parses `--tool-set` (`run_unified_runtime.py`, currently `customer` | `dev_tools`).
   `dev_tools` is renamed to `plugin_authoring` as part of this work — not added as a third choice.
2. `_resolve_tool_set(tool_set, nucore_interface)` returns `(tool_spec_paths, dispatch, system_prompt_builder)`. `customer` returns `(None, None, None)` and gets the runtime's built-in defaults.
3. Those three are passed to `UnifiedRuntime(...)` (`runtime.py`), which loads the schemas, routes tool calls through the dispatch override, and builds the prompt with the injected builder.
4. Two call sites: the WebSocket server (resolved once at startup, then one `UnifiedRuntime` per connection sharing one `SessionStore`) and the CLI (`--query` or REPL; one runtime).

Changes: rename the existing `dev_tools` choice to `plugin_authoring` (and its branch in
`_resolve_tool_set`) rather than adding a new choice. `UnifiedRuntime` and `AgenticLoop` stay
unchanged.

### Per-connection state

The resolved dispatch closure is shared by every WebSocket connection, but the evidence ledger (sources found, tiers tried, vetting rationale) must be per session. Otherwise one user's evidence could satisfy another's "credible source" check. Preferred approach: `_resolve_tool_set` returns a per-connection dispatch **factory** for this tool set, called once per connection (and once for the CLI), creating a fresh ledger. Fallback if that contract change is unwanted: key state by the stable session id from `EisyUIContext`. No module-level mutable state.

### Server-side configuration

Resolved at startup and injected via `functools.partial`, never taken from tool args or model output:

- `--plugin-output-root <dir>` (proposed flag): the allowed root. The tool set refuses to start without it.
- `--search-engine {brave,tavily}` (CLI, with a `search_engine` `runtime_config` fallback) selects
  the Brave/Tavily web-search provider; `SEARCH_ENGINE_API_KEY` environment variable supplies its
  key. Explicitly passing this always forces that fallback, on any LLM provider. Omitting it: if
  the resolved provider for this tool set is `claude`, Claude's own native `web_search` tool is
  used instead (no key needed); for any other provider, web search is unavailable and the flow
  falls through to asking for URLs -- unchanged from before native search existed.

### Isolation

There is no isolation wall — that was the pre-merge design, now superseded. The dispatch table is
the union of `dev_tools`' existing tools (`validate_profile`, `lookup_uom`, `configure_plugin`,
`plugin_ops`, `get_plugin_capabilities`, `call_plugin`, `list_installed_plugins`) and the new
discovery/generation tools below. Device-control tools stay available by design: the merge
intentionally gives the customer-facing flow the same live-hub tools the developer-facing flow
already has, rather than walling them off. A live backend is still required, as it already is for
`dev_tools`.

### Launching

Same invocation as the current `dev_tools` tool set — `--tool-set dev_tools` becomes
`--tool-set plugin_authoring` — plus the new output root:

```shell
export SEARCH_ENGINE_API_KEY=...    # optional; pair with --search-engine brave|tavily
python -m unified.run_unified_runtime \
  --runtime-config src/unified/runtime_config.example.json \
  --backend-api-classpath iox.IoXWrapper \
  --backend-api-base-url https://192.168.6.134 \
  --backend-api-username admin \
  --backend-api-password yourpassword \
  --tool-set plugin_authoring \
  --plugin-output-root ~/plugin-projects \
  --search-engine brave \
  --query "I want a plugin that exposes my pool controller"
```

Omit `--query` for the REPL, or add the WebSocket options (`--websocket-host`, `--websocket-port`, optional `--ssl-certfile`/`--ssl-keyfile`) to serve a UI. Everything else (runtime config, backend flags, `--stream`, `--max-iterations`) behaves as for the other tool sets.

## Implementation phases

### Phase 1: Skeleton and runtime wiring
- Rename `src/unified/dev_tools/` to `src/unified/plugin_authoring/` **in place** (not a new
  mirrored package) — keep its existing `__init__.py`, `dispatch.py`, `handlers/`, `tools/`,
  `prompt/`, and every tool it already ships.
- Rename the `--tool-set` choice (`dev_tools` → `plugin_authoring`) and update the
  `_resolve_tool_set` branch accordingly; add `--plugin-output-root` and the per-connection factory.
- Reuse existing store/installed listing schemas by path, as the package already does, so they cannot drift.

### Phase 2: Discovery tools (narrow schemas, pure handlers)
- `search_store_plugins`: wraps `plugin_management.list_store_plugins`, returns candidates with a viability signal. The prompt says recommend and stop unless the user asks to continue.
- `search_github_plugins`: public repo search with caps on queries and fetches; returns repo, license, last-updated, and links to API docs. Callable directly for a "find a repo for X" ask, but no longer a mandatory precursor to web search (see below).
- Web search is either Claude's own native `web_search` server tool (no second API key, used automatically whenever the resolved LLM provider for this tool set is `claude` and `--search-engine` was not explicitly given) or the Brave/Tavily `search_web` fallback (registered only when both the engine choice and its key are configured; queries pass through a secret scrubber) -- explicitly passing `--search-engine` always forces the fallback, on any provider. Non-Claude providers with no engine configured have no web search tool at all, same as before this native-search addition.
- `fetch_reference`: fetches discovered or user-supplied URLs with size and count caps; content is untrusted data.
- Evidence ledger records sources and vetting rationale. There is no longer a "web search errors until GitHub was tried" gate -- Claude's native search is a server-side tool our dispatch layer never sees invoked, so that sequencing couldn't be enforced once it existed. Instead, every search result from either path is classified by URL: a `github.com/<owner>/<repo>` result gets a follow-up license lookup via GitHub's REST API and is recorded with real license metadata, exactly like `search_github_plugins` already does; everything else is recorded plainly. The ask-for-URLs step is available once web search has been tried this session, or wasn't available to try at all. An empty ledger makes generation refuse and write nothing.
- License vetting starts with a permissive allowlist (MIT, Apache-2.0, BSD); anything else is flagged to the user and never copied.

### Phase 3: Workspace discovery (list/read generated plugins)
A customer coming back later needs the AI to pick up where it left off, not start blind -- the
"new vs. history" distinction a session-based tool (e.g. Claude Code) already gives its own users.
This phase is the discovery half only; actually modifying and rewriting a plugin still goes
through Phase 4's `generate_plugin_scaffold` and its overwrite flow below. Local-disk-only, no
external dependency, so both tools are always registered (no key/engine gating).
- `list_generated_plugins` scans immediate subdirectories of `--plugin-output-root` for ones
  containing `profile.json` (the one file Phase 4 always writes -- no separate manifest format).
  Returns each one's `location` (the subdirectory name, relative to the root -- never an absolute
  host path, so server filesystem layout isn't leaked), a best-effort `name`/`description`
  (`profile.json`'s first nodedef, falling back to `README.md`'s first heading, falling back to
  the directory name -- never raises), and `last_modified_at` (directory mtime). Sorted
  most-recently-modified first.
- `read_generated_plugin` takes a `location` from the list above, confines it against the output
  root (reject `..`, absolute escapes, symlinked parents/targets -- same checks Phase 4's output
  confinement needs for writes, factored into one shared `path_confinement.py` so Phase 4 reuses it
  rather than re-deriving the same logic), and returns `profile.json` (parsed), `plugin.py` text,
  `README.md` text, and each `tests/test_*.py` file's text. `{"error": ...}` if the location
  doesn't exist, escapes the root, or has no `profile.json`.
- Open question, not silently decided: `--plugin-output-root` is one server-wide directory with no
  per-customer namespacing anywhere in this design (today's single-hub-per-customer assumption) --
  `list_generated_plugins` surfaces everything under it. Revisit if multi-tenant deployment is ever
  in scope.

### Phase 4: Scaffold generation
- `generate_plugin_scaffold` takes a spec (nodedefs, editors, linkdefs, config fields, evidence refs) and renders `profile.json`, `plugin.py` (config placeholders only), `tests/test_*.py`, and a README citing sources.
- Profile validation must bridge the two shapes. First try wrapping the wire document as `{families:[{instances:[…]}]}` and running the existing `validate_profile`; if that loses checks, add a small local wire-shape check (required keys, id and reference consistency, nodedef-to-editor references) built on the existing `defs/` rules. No `jsonschema` production dependency unless evidence shows it is necessary.
- Output confinement: resolve real paths and require `commonpath` with the resolved allowed root; reject traversal, absolute escapes, and symlinked parents or targets. Write into a dedicated subdirectory only. Reuse Phase 3's `path_confinement.py` rather than re-deriving these checks.
- Overwrite flow: if any target exists, the first call returns the conflict list and writes nothing; a second call with `confirm_overwrite=true` proceeds, and the prompt passes that only after explicit user agreement. This is the actual "modify an existing plugin" step Phase 3's list/read tools lead into.
- Generated code is never imported or executed; at most an `ast.parse` syntax check.

### Phase 5: Prompt
Non-technical-user prompt: focused intake questions, store-first recommend-and-stop, the fallback ladder with no fabricated API behavior, no secrets in queries or code, generation only.

### Phase 6: Tests (`tests/unified/plugin_authoring/`, network mocked)
- Tier ordering and in-code enforcement; store-match stop behavior; no-source gives no files and a clear reason.
- Wire-shape validation accepts valid documents and rejects nested or malformed ones.
- Path confinement (`..`, absolute paths, symlinked directories and files); overwrite consent round trip.
- Secret scrubbing in queries; caps on queries and fetches.
- Per-connection ledger isolation (two runtimes do not share evidence).
- Runtime tool-set selection and startup refusal without an output root (extend `test_run_unified_runtime.py`).

### Phase 7: Documentation
Rename/rewrite `src/unified/dev_tools/README.md` to `src/unified/plugin_authoring/README.md`,
covering both the already-shipped developer-facing tools and the new discovery/generation tools
as one tool set. Update the top-level README's existing "Developer Tool Set (Plugin Authoring)"
section in place (flag name, path, output root, search engine + key) rather than adding a new section. Also
add a note in `src/nucore/schemas/README.md` on which shape this tool set emits.

## Key files

- `src/unified/run_unified_runtime.py`, `src/unified/runtime.py`, and `src/unified/dev_tools/` (the package being renamed to `plugin_authoring` in place, not mirrored).
- `src/unified/handlers/plugin_management.py` (store discovery).
- `src/nucore/schemas/dynamic_profile_update.schema.json`, `src/nucore/schemas/nucore_profile_catalog.schema.json`, `src/nucore/profile.py` (validation bridge).

## Research and safety notes

- Bound automated research (caps per tier), stop once evidence is sufficient, report source links and vetting rationale.
- No writes outside the allowed root; keep output in a dedicated directory; confirm before replacing.
- Generated Python and external repositories are untrusted: not imported or executed during discovery or generation, and no contact with external device APIs.
- Generated profiles must be the wire format, not the nested catalog; test the intended format explicitly.

## Open decisions

1. Validation: wrap-and-reuse `validate_profile` first, local wire-shape check as fallback (recommended).
2. Per-connection dispatch factory vs. session-id keyed state (factory recommended).
3. Build order: confinement, overwrite handling, and validation first, then discovery.
