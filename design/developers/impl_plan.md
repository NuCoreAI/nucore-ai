# Conversational Plugin Authoring Prototype — Implementation Plan

## Problem and approach

Build a prototype for a separate, non-technical-customer-facing plugin-authoring tool set, selected with `--tool-set plugin_authoring`. It asks focused intake questions, checks the NuCore plugin store first, and recommends a viable existing plugin rather than generating a duplicate. If the user explicitly wants a custom plugin, it seeks credible implementation evidence through a bounded fallback chain. It must not invent undocumented API behavior, and it creates no files when no credible source is available.

For a viable custom request it generates a small scaffold: a Dynamic Profiles JSON document, a Python backend, pytest tests, and a README. The profile document uses the Dynamic Profiles **wire payload** shape (top-level `editors`, `nodedefs`, `linkdefs`), not NuCore's internal catalog shape (`families[]` → `instances[]` → definitions).

The prototype is generation-only: it never installs, starts, executes, or live-tests generated code on the hub.

## Decisions confirmed with the user

- Deliver a scaffold: Dynamic Profile JSON, Python backend, pytest tests, README.
- Output goes to a user-selected directory restricted to a configured allowed root.
- A viable store match is recommended first and the flow stops; a custom plugin is generated only if the user asks to continue.
- Fallback order after no viable store match:
  1. GitHub search for eisy/IoX/NuCore plugin repositories, following references to official API documentation.
  2. Brave Search, using a server-side API key.
  3. Ask the user for URLs.
  4. If no credible source results, report why and create no files.
- Public plugin source is a baseline only after license, quality, and compatibility vetting; preserve notices, never copy incompatible or unlicensed code.
- Existing output files: warn and require explicit user confirmation before overwriting.
- Never put user credentials/secrets in search queries, generated source, prompts, or logs; generated code uses configuration placeholders.

## How it is instantiated and launched

### Wiring (existing pattern)

A tool set is plain constructor injection into `UnifiedRuntime`; nothing registers it.

1. `main()` parses `--tool-set` (`run_unified_runtime.py`, currently `customer` | `dev_tools`).
2. `_resolve_tool_set(tool_set, nucore_interface)` returns `(tool_spec_paths, dispatch, system_prompt_builder)`. `customer` returns `(None, None, None)` and gets the runtime's built-in defaults.
3. Those three are passed to `UnifiedRuntime(...)` (`runtime.py`), which loads the schemas, routes tool calls through the dispatch override, and builds the prompt with the injected builder.
4. Two call sites: the WebSocket server (resolved once at startup, then one `UnifiedRuntime` per connection sharing one `SessionStore`) and the CLI (`--query` or REPL; one runtime).

Changes for `plugin_authoring`: add the `choices` entry and a branch in `_resolve_tool_set`. `UnifiedRuntime` and `AgenticLoop` stay unchanged.

### Per-connection state

The resolved dispatch closure is shared by every WebSocket connection, but the evidence ledger (sources found, tiers tried, vetting rationale) must be per session. Otherwise one user's evidence could satisfy another's "credible source" check. Preferred approach: `_resolve_tool_set` returns a per-connection dispatch **factory** for this tool set, called once per connection (and once for the CLI), creating a fresh ledger. Fallback if that contract change is unwanted: key state by the stable session id from `EisyUIContext`. No module-level mutable state.

### Server-side configuration

Resolved at startup and injected via `functools.partial`, never taken from tool args or model output:

- `--plugin-output-root <dir>` (proposed flag): the allowed root. The tool set refuses to start without it.
- `BRAVE_API_KEY` environment variable (proposed): enables the Brave tier. Absent means the tier is unavailable and the flow falls through to asking for URLs.

### Isolation

The dispatch table exposes only the store-listing handlers plus the authoring tools. It does not expose device-control handlers or the rest of `NuCoreInterface`, and it is separate from `dev_tools`. A live backend is still required because the store listing goes through `NuCoreInterface`.

### Launching

Same invocation as `dev_tools`, with the new tool set and output root:

```shell
export BRAVE_API_KEY=...            # optional; enables the Brave tier
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

Omit `--query` for the REPL, or add the WebSocket options (`--websocket-host`, `--websocket-port`, optional `--ssl-certfile`/`--ssl-keyfile`) to serve a UI. Everything else (runtime config, backend flags, `--stream`, `--max-iterations`) behaves as for the other tool sets.

## Implementation phases

### Phase 1: Skeleton and runtime wiring
- Create `src/unified/plugin_authoring/` mirroring `dev_tools`: `__init__.py`, `dispatch.py`, `handlers/`, `tools/`, `prompt/`.
- Add the `--tool-set` choice, the `_resolve_tool_set` branch, `--plugin-output-root`, and the per-connection factory.
- Reuse existing store/installed listing schemas by path, as `dev_tools` does, so they cannot drift.

### Phase 2: Discovery tools (narrow schemas, pure handlers)
- `search_store_plugins`: wraps `plugin_management.list_store_plugins`, returns candidates with a viability signal. The prompt says recommend and stop unless the user asks to continue.
- `search_github_plugins`: public repo search with caps on queries and fetches; returns repo, license, last-updated, and links to API docs.
- `search_web` (Brave): registered only when the key is configured; queries pass through a secret scrubber.
- `fetch_reference`: fetches discovered or user-supplied URLs with size and count caps; content is untrusted data.
- Evidence ledger records sources and vetting rationale. Tier order is enforced in code: Brave errors until GitHub was tried; the ask-for-URLs step is available only after both. An empty ledger makes generation refuse and write nothing.
- License vetting starts with a permissive allowlist (MIT, Apache-2.0, BSD); anything else is flagged to the user and never copied.

### Phase 3: Scaffold generation
- `generate_plugin_scaffold` takes a spec (nodedefs, editors, linkdefs, config fields, evidence refs) and renders `profile.json`, `plugin.py` (config placeholders only), `tests/test_*.py`, and a README citing sources.
- Profile validation must bridge the two shapes. First try wrapping the wire document as `{families:[{instances:[…]}]}` and running the existing `validate_profile`; if that loses checks, add a small local wire-shape check (required keys, id and reference consistency, nodedef-to-editor references) built on the existing `defs/` rules. No `jsonschema` production dependency unless evidence shows it is necessary.
- Output confinement: resolve real paths and require `commonpath` with the resolved allowed root; reject traversal, absolute escapes, and symlinked parents or targets. Write into a dedicated subdirectory only.
- Overwrite flow: if any target exists, the first call returns the conflict list and writes nothing; a second call with `confirm_overwrite=true` proceeds, and the prompt passes that only after explicit user agreement.
- Generated code is never imported or executed; at most an `ast.parse` syntax check.

### Phase 4: Prompt
Non-technical-user prompt: focused intake questions, store-first recommend-and-stop, the fallback ladder with no fabricated API behavior, no secrets in queries or code, generation only.

### Phase 5: Tests (`tests/unified/plugin_authoring/`, network mocked)
- Tier ordering and in-code enforcement; store-match stop behavior; no-source gives no files and a clear reason.
- Wire-shape validation accepts valid documents and rejects nested or malformed ones.
- Path confinement (`..`, absolute paths, symlinked directories and files); overwrite consent round trip.
- Secret scrubbing in queries; caps on queries and fetches.
- Per-connection ledger isolation (two runtimes do not share evidence).
- Runtime tool-set selection and startup refusal without an output root (extend `test_run_unified_runtime.py`).

### Phase 6: Documentation
`src/unified/plugin_authoring/README.md`, a main README section next to the `dev_tools` one (launching, output root, Brave key), and a note in `src/nucore/schemas/README.md` on which shape this tool set emits.

## Key files

- `src/unified/run_unified_runtime.py`, `src/unified/runtime.py`, and `src/unified/dev_tools/` (pattern to mirror).
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
