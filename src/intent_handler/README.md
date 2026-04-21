# Intent Handler Infrastructure Guide

This is the canonical documentation for the standalone intent-handler infrastructure.

The runtime intent workspace under `src/intent_handler_directory` should contain only intent implementations and runtime config. Full architecture, router, provider, and runtime behavior documentation is centralized here.

## Operator Quickstart

Use this section if you only need to run and verify the system.

### Prerequisites

- Python environment is active
- `runtime_config.json` exists in `src/intent_handler/runtime_assets`
- API keys are set for providers you plan to use

Example environment variables:

```bash
export OPENAI_API_KEY="..."
export ANTHROPIC_API_KEY="..."
export GEMINI_API_KEY="..."
export XAI_API_KEY="..."
```

### Run Interactive Mode

```bash
python -m intent_handler.run_intent_runtime \
  --intent-dir src/intent_handler_directory \
  --provider claude \
  --api-key sk-ant-api03-...
```

### Run Single Query

```bash
python -m intent_handler.run_intent_runtime \
  --intent-dir src/intent_handler_directory \
  --provider claude \
  --api-key sk-ant-api03-... \
  --query "Turn on patio lights"
```

### Run with Streaming

Add `--stream` to print tokens as they are generated. Supported by all providers (Claude, OpenAI, Grok, llama.cpp, Gemini).

```bash
python -m intent_handler.run_intent_runtime \
  --provider claude \
  --api-key sk-ant-api03-... \
  --stream \
  --query "Turn on patio lights"
```

### Run with NuCore Backend

```bash
python -m intent_handler.run_intent_runtime \
  --provider claude \
  --api-key sk-ant-api03-... \
  --backend-api-classpath iox.IoXWrapper \
  --backend-api-base-url https://192.168.6.134 \
  --backend-api-username admin \
  --backend-api-password yourpassword \
  --json-output true
```

### CLI Flags

| Flag | Description |
|---|---|
| `--intent-dir` | Path to intent handler directory |
| `--runtime-config` | Path to `runtime_config.json` |
| `--query` | Single query mode; omit for interactive loop |
| `--provider` | LLM provider override: `claude`, `openai`, `gemini`, `grok`, `llama.cpp` |
| `--model` | Model name override |
| `--api-key` | API key override |
| `--stream` | Stream tokens to stdout as they arrive |
| `--backend-api-classpath` | Python class path for backend (e.g. `iox.IoXWrapper`) |
| `--backend-api-base-url` | Base URL for backend API |
| `--backend-api-username` | Backend API username |
| `--backend-api-password` | Backend API password |
| `--json-output` | Enable JSON output mode for backend API |
| `--prompt_type` | Prompt variant (e.g. `shared-features`) |

### Output Behavior

By default the runtime prints the extracted plain text from the model response. If streaming is enabled, tokens are printed as they arrive and the final newline is added after completion. The full structured response object is only printed when no text field can be extracted.

### Quick Verification Checklist

1. Router returns a valid intent and does not fail schema validation.
2. Pipeline dependency intents (`routable: false`) are hidden from the router but run automatically via `previous_dependencies`.
3. Prompt placeholders are resolved (common modules and runtime replacements).
4. Tool-bearing intents send provider-converted tool schemas automatically.
5. Final output is returned from the last step in the execution chain.

## 1. What This Infrastructure Does

The infrastructure provides:

- Runtime discovery of intent handlers from directory structure
- LLM-based routing to a single target intent
- Ordered dependency execution before the target intent runs
- Per-intent LLM/provider selection from runtime config
- Automatic prompt loading and runtime placeholder injection
- Automatic tool loading and provider-specific tool schema conversion

Main runtime code lives in:

- `src/intent_handler/runtime.py`
- `src/intent_handler/router.py`
- `src/intent_handler/loader.py`
- `src/intent_handler/base.py`
- `src/intent_handler/tools_handlers/*`

## 2. High-Level Execution Flow

For each user query:

1. Refresh registry and runtime config
2. Build router prompt from `src/intent_handler/runtime_assets/router/prompt.md`
3. Inject discovered intents and routing patterns
4. Route query to exactly one intent using JSON output contract (`src/intent_handler/runtime_assets/router/tool_router.json`)
5. Build execution chain from `previous_dependencies`
6. For each step in chain:
7. Instantiate handler
8. Resolve step LLM config (default + override)
9. Render prompt with runtime replacements from handler
10. Auto-load configured tools and convert to selected provider format
11. Execute handler
12. Store output as dependency context for next step
13. Return final step result

## 3. Directory Layout Contract

`IntentHandlerRegistry` treats only directories with `config.json` as runnable intents.

Each runnable intent directory must contain:

- `config.json`
- `prompt.md`
- `handler.py`

Router and memory-store assets are kept under `src/intent_handler/runtime_assets/*` and are not discovered as intents.

## 4. Root Runtime Files

### 4.1 runtime_config.json

Controls available LLM definitions and per-intent LLM routing.

Path:

- `src/intent_handler/runtime_assets/runtime_config.json`

Schema:

```json
{
  "supported_llms": {
    "openai": {
      "provider": "openai",
      "model": "gpt-4.1-mini",
      "url": null,
      "params": {
        "temperature": 0.1,
        "max_tokens": 4096,
        "supports_system_role": true
      },
      "api_key": null
    }
  },
  "default_llm": "openai",
  "router_llm": "openai"
}
```

Parameter details:

- `supported_llms`: map of named LLM profiles
- `default_llm`: fallback key in `supported_llms`
- `router_llm`: optional router-specific key in `supported_llms`
- Per-intent selection is defined in each intent `config.json` via `llm_override`

`supported_llms.<key>` fields:

- `provider` or `llm`: provider name alias
- `model`: provider model id
- `url`: optional base URL override
- `params`: call defaults merged into intent call config
- `api_key`: optional explicit key (otherwise env vars are used)

Known provider aliases:

- `anthropic` -> `claude`
- `gpt` -> `openai`
- `google` -> `gemini`
- `xai` or `x.ai` -> `grok`
- `llamacpp` or `llama_cpp` -> `llama.cpp`

### 4.2 Per-Intent LLM Override (Optional)

Each intent `config.json` may include:

- `llm_override`: optional key that must exist in `runtime_config.supported_llms` when provided

If omitted, runtime falls back to:

1. `default_llm`
2. first available entry in `supported_llms`

## 5. Router Infrastructure

Router assets live in:

- `src/intent_handler/runtime_assets/router/prompt.md`
- `src/intent_handler/runtime_assets/router/tool_router.json`

Router prompt supports these runtime placeholders:

- `<<DISCOVERED_INTENTS>>`
- `<<ROUTING_PATTERNS>>`
- Common module placeholders (for example `<<nucore_definitions>>`)

Router output requirements:

- JSON only
- Must conform to `tool_router.json` input schema
- Required fields: `intent`, `user_query`
- Extra fields are rejected when schema has `additionalProperties: false`

Note:

- The router does not use tool-calling transport.
- It enforces tool schema conformance for JSON output payload.

## 6. Intent config.json Reference

Each intent `config.json` supports:

- `intent` (required): must match directory name
- `description`: displayed in router discovered intent blocks
- `handler`: python file name, default `handler.py`
- `handler_class`: optional explicit class name in handler file
- `routable`: set to `false` to hide this intent from the router while still allowing it to be invoked as a dependency. Default: `true`. Use this for pipeline filter intents that are only ever triggered via `previous_dependencies`.
- `previous_dependencies`: ordered list of prerequisite intents
- `routing_examples`: used in router prompt generation (only when `routable` is `true`)
- `router_hints`: used in router prompt generation (only when `routable` is `true`)
- `tool_files`: optional list of tool JSON files (relative or absolute). Runtime also auto-discovers `tool_*.json` inside the intent directory and merges both lists with de-duplication.
- `llm_override`: optional LLM profile key in `runtime_config.supported_llms`
- `llm_config`: per-intent call defaults merged with runtime selection

Validation enforced:

- Intent directory name must equal `config.intent`
- Every dependency must exist
- Self-dependency is invalid
- Cycles are invalid

Tool file resolution behavior:

- Files explicitly listed in `tool_files` are loaded first
- Files matching `tool_*.json` in the intent directory are auto-discovered and appended
- Duplicate paths are removed during merge

## 7. Prompt Processing

Prompt source for each intent:

- Read from `prompt.md`
- Expand shared common modules
- Later, at call time, inject dynamic runtime placeholders from handler method

### 7.1 Shared Common Modules

Common module files live in:

- `src/intent_handler/common_modules`

Each `.md` file becomes a module key by filename stem.

Example:

- `definitions.md` => `definitions`
- `security.md` => `security`
- `common.md` => `common`

Supported placeholder aliases for each module key:

- `<<module_name>>`
- `<<nucore_module_name>>`
- `<<nucore_module_name_rules>>`
- Singular nucore aliases are also recognized for plural module names

## 8. Handler Contract

All handlers must subclass `BaseIntentHandler` and implement:

- `async def handle(self, query, *, route_result=None, framework_context=None, dependency_outputs=None)`

Optional but recommended:

- `def get_prompt_runtime_replacements(self, query, *, dependency_outputs: IntentHandlerResult | str | dict[str, Any] | None = None, framework_context=None, route_result=None) -> dict[str, str]`

### 8.1 Dynamic Prompt Injection

`BaseIntentHandler` calls this flow automatically in `build_messages`:

1. `render_prompt_text(...)`
2. `get_prompt_runtime_replacements(...)`
3. Replace placeholders in prompt text before sending to model

Replacement key forms accepted:

- Raw key, for example `nucore_routines_runtime`
- Full placeholder, for example `<<nucore_routines_runtime>>`

### 8.2 `framework_context`

`framework_context` is an optional free-form string that the **caller** of `handle_query` can supply. It is injected verbatim into the user turn of every LLM message as a labelled section:

```
────────────────────────────────
# FRAMEWORK CONTEXT:
<content>
```

It is intended for external state or environment information that the calling application (not the AI) knows about — for example session data, user preferences, or system status. It is passed unchanged through the runtime into every handler in the execution chain.

Example — passing session context from the caller:

```python
result = await runtime.handle_query(
    "Turn on the patio lights",
    framework_context="User is authenticated. Location: home. Time zone: America/Los_Angeles.",
)
```

When `framework_context` is `None` (the default) the section is omitted entirely from the message.

### 8.3 `extra_user_sections`

`extra_user_sections` is an optional `dict[str, str]` parameter on `build_messages` that lets a handler inject **arbitrary named sections** into the user turn, in addition to `framework_context`. Each key becomes a section heading (uppercased) and each value becomes the section body:

```
────────────────────────────────
# SECTION_NAME:
<content>
```

Sections with empty or `None` values are skipped. They appear between `framework_context` and the final `USER QUERY` block.

Example — injecting a live backend snapshot:

```python
async def handle(self, query, *, route_result=None, framework_context=None, dependency_outputs=None):
    snapshot = self.backend_api.get_status_snapshot()  # runtime data from the backend
    messages = self.build_messages(
        query,
        framework_context=framework_context,
        route_result=route_result,
        extra_user_sections={
            "backend_snapshot": snapshot,
            "active_alerts": self.backend_api.get_alerts() or "",
        },
    )
    return await self.call_llm(messages=messages)
```

Resulting user turn structure:

```
────────────────────────────────
# FRAMEWORK CONTEXT:
...
────────────────────────────────
# BACKEND_SNAPSHOT:
...
────────────────────────────────
# ACTIVE_ALERTS:
...
────────────────────────────────
# USER QUERY:
Turn on the patio lights
```

### 8.4 Automatic Tool Loading and Conversion

Handlers do not need to manually load tools.

`BaseIntentHandler.call_llm(...)` does this automatically when `tools` is not passed explicitly:

1. Read configured `tool_files` from intent config
2. Auto-discover `tool_*.json` files in the intent directory and merge with configured files
3. Load canonical tool specs using `ToolLoader.from_files`
4. Resolve effective provider from merged LLM config
5. Convert tool schemas to provider format using `create_tools_adapter(provider)`
6. Pass converted tools to LLM adapter

Introspection helpers available to handlers:

- `get_declared_tool_paths()`
- `get_tool_specs()`
- `get_tool_names()`
- `build_provider_tools()`

## 9. Tool Authoring Format

Tool JSON files in `tool_files` **must use** canonical Claude-style schema:

```json
{
  "name": "tool_name",
  "description": "What this tool does",
  "input_schema": {
    "type": "object",
    "required": [],
    "properties": {},
    "additionalProperties": false
  }
}
```

Provider conversion is performed by `intent_handler.tools_handlers` adapters.

Tool call normalization:

- Tool definitions are authored in canonical Claude schema (`name`, `description`, `input_schema`)
- Provider adapters convert request schemas for provider compatibility
- Responses from providers are parsed and normalized back to canonical Claude tool call shape before downstream handling

Canonical tool call shape used at runtime:

```json
{
  "type": "tool_use",
  "id": "call_id",
  "name": "tool_name",
  "input": {}
}
```

Output shapes by provider:

- `openai` and `grok`: OpenAI function tool format
- `llama.cpp`: OpenAI-compatible function format
- `gemini`: `functionDeclarations` tool format
- `claude`: native Claude tool format

## 10. LLM Config Resolution Order

At execution time, effective call config is merged in this order:

1. Runtime selected LLM profile (`runtime_config.json`)
2. Intent `llm_config` from `config.json`
3. Per-call override passed to `call_llm(config=...)`

Last write wins.

## 11. Dependency Pipeline Behavior

`previous_dependencies` is ordered and executed depth-first with cycle protection.

If target intent is `A` and dependencies are `B -> C`, execution order is:

- `C`
- `B`
- `A`

Each completed step contributes dependency output to `framework_context` for subsequent steps.

## 12. Runtime Bootstrap

CLI entrypoint:

- `src/intent_handler/run_intent_runtime.py`

Examples:

```bash
# Interactive loop
python -m intent_handler.run_intent_runtime --provider claude --api-key sk-ant-...

# Single query
python -m intent_handler.run_intent_runtime --query "Turn on patio lights" --provider claude --api-key sk-ant-...

# With backend and streaming
python -m intent_handler.run_intent_runtime \
  --provider claude --api-key sk-ant-... \
  --stream \
  --backend-api-classpath iox.IoXWrapper \
  --backend-api-base-url https://192.168.6.134 \
  --backend-api-username admin \
  --backend-api-password yourpassword \
  --json-output true
```

## 13. Environment Variables for Provider Clients

When `api_key` is not set in runtime config, provider clients use env vars:

- OpenAI: `OPENAI_API_KEY`
- Claude: `ANTHROPIC_API_KEY`
- Grok: `XAI_API_KEY` or `GROK_API_KEY`
- Gemini: `GEMINI_API_KEY`
- llama.cpp: optional `LLAMACPP_API_KEY`, defaults to `no-key` for local setups

## 14. Adding a New Intent

1. Create folder under `src/intent_handler_directory/<intent_name>`
2. Add `config.json`
3. Add `prompt.md`
4. Add `handler.py` with a single `BaseIntentHandler` subclass
5. Optionally add `tool_*.json` and reference via `tool_files`
6. Optionally add runtime placeholders and implement `get_prompt_runtime_replacements`
7. Optionally add `routing_examples` and `router_hints` for better routing
8. Optionally add intent LLM override in `runtime_config.json`

## 16. Session Management and Conversation History

`IntentRuntime` maintains per-session conversation history through a built-in `SessionStore`. History is global across intents within a session — not per-intent.

### How it works

1. The caller passes a `session_id` string to `handle_query`.
2. `SessionStore` returns (or creates) a `ConversationHistory` for that ID.
3. Before each handler runs, `BaseIntentHandler.set_current_history()` loads the history.
4. `build_messages()` prepends prior turns as alternating `user` / `assistant` messages between the system prompt and the current user turn.
5. After the final result is returned, the query and response text are appended to the history for future turns.

If `session_id` is `None` (default), no history is loaded or stored — each call is stateless.

### Classes

| Class | Location | Purpose |
|---|---|---|
| `ConversationTurn` | `models.py` | Single `(query, response)` pair |
| `ConversationHistory` | `models.py` | Ordered list of turns with auto-pruning |
| `SessionStore` | `session_store.py` | In-memory dict keyed by session ID |

`IntentRuntime.session_store` is a public attribute and can be replaced with a custom store if needed (e.g. Redis-backed).

### Pruning

Each `ConversationHistory` caps itself at `max_turns` (oldest turns discarded). The limit is resolved in this order:

1. `max_turns` on the active LLM entry in `runtime_config.json`
2. `default_max_turns` at the root of `runtime_config.json`
3. Hardcoded fallback: `20`

The limit is applied when the session is first created. If the provider is switched mid-session the existing history object keeps its original limit.

### runtime_config.json fields

```json
{
  "supported_llms": {
    "claude": {
      "max_turns": 20,
      ...
    }
  },
  "default_max_turns": 20
}
```

### Caller example

```python
result = await runtime.handle_query(
    "Turn on the patio lights",
    session_id="user-abc123",
)

# Clear history for a session
runtime.session_store.clear("user-abc123")

# Clear all sessions
runtime.session_store.clear_all()
```

### CLI

The interactive loop (`_run_loop`) uses `session_id="default"` automatically, so every interactive turn is part of the same conversation. Single-query mode (`--query`) does not pass a session ID and remains stateless.

### Message layout with history

For providers that support a system role:

```
[system]  <prompt text>
[user]    <turn 1 query>
[assistant] <turn 1 response>
[user]    <turn 2 query>
[assistant] <turn 2 response>
...
[user]    <current query with framework_context / extra_user_sections>
```

For providers that do not support a system role (e.g. Claude in raw message mode), system instructions are prepended to the first user message only, keeping the alternating `user`/`assistant` shape intact.

- Missing required files: intent directory missing `prompt.md` or `handler.py`
- Handler class ambiguity: more than one `BaseIntentHandler` subclass and no `handler_class`
- Unknown dependency in `previous_dependencies`
- Circular dependency chain
- Runtime config override references unknown intent or unknown llm key
- Router response JSON missing required `tool_router` fields
- Tool spec missing `input_schema`

## 17. Streaming Support

All provider adapters support an optional streaming mode. When `--stream` is passed on the CLI:

- The runtime injects `stream: true` and a `stream_handler` callback into each LLM config before the call.
- Each adapter emits text chunks via the callback as they arrive.
- After streaming completes, the adapter returns the same normalized response shape (`content`, `text`, `raw`) so downstream handler logic is unaffected.
- Gemini streaming falls back to a non-streaming request if the endpoint does not expose `streamGenerateContent`.

Provider-specific streaming implementation:

| Provider | Implementation |
|---|---|
| `claude` | `AsyncAnthropic.messages.stream()` |
| `openai` | `chat.completions.create(stream=True)` |
| `grok` | Same as OpenAI (OpenAI-compatible) |
| `llama.cpp` | Same as OpenAI (OpenAI-compatible) |
| `gemini` | `streamGenerateContent` SSE endpoint with fallback |

## 18. Current Runnable Intents

The following intents live under `src/intent_handler_directory`:

### Routable Intents (visible to the router)

| Intent | Description | Dependencies |
|---|---|---|
| `command_control_status` | Device commands and real-time status | `device_filter` |
| `routine_automation` | Create or edit routine logic | `device_filter` |
| `routine_status_ops` | Enable/disable/run existing routines | `routine_filter` |
| `group_scene_operations` | Group and scene queries | `device_filter` |
| `general_help` | Conceptual help, definitions, non-execution queries | — |

### Pipeline Filter Intents (`routable: false` — dependency-only)

| Intent | Description | Used by |
|---|---|---|
| `device_filter` | Narrows device candidates for downstream execution | `command_control_status`, `routine_automation`, `group_scene_operations` |
| `routine_filter` | Narrows routine candidates for downstream execution | `routine_status_ops` |

Pipeline filter intents are never presented to the router LLM. They are invoked automatically via `previous_dependencies` when a routable intent that depends on them is selected.

Runtime utility assets:

- `src/intent_handler/runtime_assets/router`
- `src/intent_handler/runtime_assets/memory_store`
