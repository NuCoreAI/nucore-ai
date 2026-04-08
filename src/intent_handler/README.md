# Intent Handler Infrastructure Guide

This is the canonical documentation for the standalone intent-handler infrastructure.

The runtime intent workspace under `src/intent_handler_directory` should contain only intent implementations and runtime config. Full architecture, router, provider, and runtime behavior documentation is centralized here.

## Operator Quickstart

Use this section if you only need to run and verify the system.

### Prerequisites

- Python environment is active
- `runtime_config.json` exists in `src/intent_handler_directory`
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
  --intent-dir src/intent_handler_directory
```

### Run Single Query

```bash
python -m intent_handler.run_intent_runtime \
  --intent-dir src/intent_handler_directory \
  --query "Turn on patio lights"
```

### Point to Explicit Runtime Config

```bash
python -m intent_handler.run_intent_runtime \
  --intent-dir src/intent_handler_directory \
  --runtime-config src/intent_handler/runtime_assets/runtime_config.json
```

### Quick Verification Checklist

1. Router returns a valid intent and does not fail schema validation.
2. Dependency intents run before the target intent when configured.
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
- `previous_dependencies`: ordered list of prerequisite intents
- `routing_examples`: used in router prompt generation
- `router_hints`: used in router prompt generation
- `tool_files`: list of tool json files relative to intent directory
- `llm_override`: optional LLM profile key in `runtime_config.supported_llms`
- `llm_config`: per-intent call defaults merged with runtime selection

Validation enforced:

- Intent directory name must equal `config.intent`
- Every dependency must exist
- Self-dependency is invalid
- Cycles are invalid

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

- `async def handle(self, query, *, route_result=None, framework_context=None)`

Optional but recommended:

- `def get_prompt_runtime_replacements(self, query, *, framework_context=None, route_result=None) -> dict[str, str]`

### 8.1 Dynamic Prompt Injection

`BaseIntentHandler` calls this flow automatically in `build_messages`:

1. `render_prompt_text(...)`
2. `get_prompt_runtime_replacements(...)`
3. Replace placeholders in prompt text before sending to model

Replacement key forms accepted:

- Raw key, for example `nucore_routines_runtime`
- Full placeholder, for example `<<nucore_routines_runtime>>`

### 8.2 Automatic Tool Loading and Conversion

Handlers do not need to manually load tools.

`BaseIntentHandler.call_llm(...)` does this automatically when `tools` is not passed explicitly:

1. Read `tool_files` from intent config
2. Load canonical tool specs using `ToolLoader.from_files`
3. Resolve effective provider from merged LLM config
4. Convert tool schemas to provider format using `create_tools_adapter(provider)`
5. Pass converted tools to LLM adapter

Introspection helpers available to handlers:

- `get_declared_tool_paths()`
- `get_tool_specs()`
- `get_tool_names()`
- `build_provider_tools()`

## 9. Tool Authoring Format

Tool JSON files in `tool_files` must use canonical Claude-style schema:

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
python -m intent_handler.run_intent_runtime --intent-dir src/intent_handler_directory
python -m intent_handler.run_intent_runtime --query "Turn on patio lights"
python -m intent_handler.run_intent_runtime --runtime-config src/intent_handler/runtime_assets/runtime_config.json
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

## 15. Common Failure Modes

- Missing required files: intent directory missing `prompt.md` or `handler.py`
- Handler class ambiguity: more than one `BaseIntentHandler` subclass and no `handler_class`
- Unknown dependency in `previous_dependencies`
- Circular dependency chain
- Runtime config override references unknown intent or unknown llm key
- Router response JSON missing required `tool_router` fields
- Tool spec missing `input_schema`

## 16. Current Runnable Intents

Current runnable intent folders are under `src/intent_handler_directory`.

Runtime utility assets:

- `src/intent_handler/runtime_assets/router`
- `src/intent_handler/runtime_assets/memory_store`
