# Intent Handler Infrastructure Guide

This document is the canonical guide for the standalone intent-handler runtime.

## Quick Start

### 1) Create a runtime profile JSON

Pass a JSON file with top-level `nucore_runtime` to `--runtime-config`.

Example profile:

```json
{
  "nucore_runtime": {
    "default": {
      "provider": "claude",
      "model": "claude-sonnet-4-20250514",
      "api_key": "${ANTHROPIC_API_KEY}",
      "url": null,
      "max_turns": 20,
      "temperature": 0.2,
      "max_tokens": 32000
    },
    "router": {
      "provider": "claude",
      "model": "claude-haiku-4-5-20251001",
      "api_key": "${ANTHROPIC_API_KEY}",
      "url": null,
      "max_turns": 20,
      "temperature": 0.2,
      "max_tokens": 32000
    },
    "routine_automation": {
      "provider": "openai",
      "model": "gpt-5-mini-2025-08-07",
      "api_key": "${OPENAI_API_KEY}",
      "url": null,
      "max_turns": 20,
      "temperature": 0.1,
      "max_tokens": 32000
    }
  }
}
```

Reference: `src/intent_handler/runtime_assets/nucore_runtime.example.json`.

### 2) Run interactive mode

```bash
python -m intent_handler.run_intent_runtime \
  --intent-dir src/intent_handler_directory \
  --runtime-config src/intent_handler/runtime_assets/nucore_runtime.example.json
```

### 3) Run one query

```bash
python -m intent_handler.run_intent_runtime \
  --intent-dir src/intent_handler_directory \
  --runtime-config src/intent_handler/runtime_assets/nucore_runtime.example.json \
  --query "Turn on patio lights"
```

### 4) Run with NuCore backend

```bash
python -m intent_handler.run_intent_runtime \
  --intent-dir src/intent_handler_directory \
  --runtime-config src/intent_handler/runtime_assets/nucore_runtime.example.json \
  --secrets_file /path/to/secrets.json \
  --backend-api-classpath iox.IoXWrapper \
  --backend-api-base-url https://192.168.6.134 \
  --backend-api-username admin \
  --backend-api-password yourpassword \
  --json-output true
```

### 5) Optional secrets file

`--secrets_file` points to a JSON object of key/value pairs used for provider
API-key lookup (forwarded into dispatch builder environment resolution).

The file must be valid JSON, not shell `KEY=VALUE` syntax. Use a flat object
with string values only. Comments and nested objects are not supported.

Example:

```json
{
  "OPENAI_API_KEY": "...",
  "ANTHROPIC_API_KEY": "...",
  "GEMINI_API_KEY": "...",
  "XAI_API_KEY": "..."
}
```

Format rules:

- Top level must be a JSON object.
- Keys should be provider-style secret names such as `ANTHROPIC_API_KEY`.
- Values should be strings.
- If you need multiple names for the same secret, repeat the string under each key.

## Streaming

Streaming is always enabled via runtime wiring and profile resolution.

- No `--stream` CLI switch is required.
- Router calls use `RouterStreamHandler`.
- Intent calls use the active handler stream callback.

## CLI Reference

| Flag | Description |
|---|---|
| `--intent-dir` | Path to intent handler directory |
| `--runtime-config` | Required path to JSON with top-level `nucore_runtime` |
| `--secrets_file` | Optional JSON file of key/value pairs used for provider key resolution |
| `--query` | Single query mode; omit for interactive loop |
| `--backend-api-classpath` | Python class path for backend (for example `iox.IoXWrapper`) |
| `--backend-api-base-url` | Base URL for backend API |
| `--backend-api-username` | Backend API username |
| `--backend-api-password` | Backend API password |
| `--json-output` | Enable JSON output mode for backend API |
| `--prompt_type` | Prompt variant (for example `shared-features`) |
| `--log-level` | Logging level override |
| `--log-file` | Optional rotating log file path |
| `--log-json` | Emit logs in JSON format |
| `--no-log-console` | Disable console logging |

## Runtime Resolution Rules

1. Router uses `nucore_runtime.router` when present, otherwise `nucore_runtime.default`.
2. Intent execution:
- If `nucore_runtime.<intent_name>` exists, runtime uses it fully.
- Otherwise runtime uses `nucore_runtime.default` and overlays intent `config.json` `llm_config`.

Runtime profiles are provider-only: use `provider` in each profile and do not rely on legacy `llm` aliases or `supported_llms` fallback behavior.

## Directory Layout

Only folders with `config.json` are discovered as runnable intents.

Each runnable intent folder must contain:

- `config.json`
- `prompt.md`
- `handler.py`

## Intent `config.json` Fields

| Field | Description |
|---|---|
| `intent` | Required, must match directory name |
| `description` | Router-visible description |
| `handler` | Handler Python file (default `handler.py`) |
| `handler_class` | Optional explicit class in handler file |
| `routable` | `false` hides intent from router |
| `routing_examples` | Router examples |
| `router_hints` | Router hints |
| `tool_files` | Extra tool files; runtime also auto-discovers `tool_*.json` |
| `llm_config` | Per-intent overlay fields used when no full intent profile exists |

## Handler Contract

All handlers must subclass `BaseIntentHandler` and implement:

```python
async def handle(
  self,
  query,
  *,
  route_result=None,
  framework_context=None,
  raw_response=None,
  tool_calls=None,
):
    ...
```

Runtime execution model:

1. Runtime calls `handler.build_messages(...)`.
2. Runtime calls `handler.call_llm(messages=...)`.
3. Runtime extracts tool calls from the raw response.
4. Runtime calls `handler.handle(...)` for post-processing, passing:
   - `raw_response`: the result returned by `call_llm`
   - `tool_calls`: extracted tool calls (processed one-by-one by runtime)

Handlers should treat `handle(...)` as a post-processing step (tool execution,
result transformation, validation), not as the place to call the LLM.

Optional runtime prompt replacement hook:

```python
def get_prompt_runtime_replacements(self, query, *, framework_context=None, route_result=None) -> dict[str, str]:
    return {}
```

Optional tool-result/agent-response prompt hook:

```python
async def get_tool_result_prompt(self) -> str | None:
  """Return a dedicated prompt template for tool-result follow-up."""
  return None
```

When non-`None`, runtime uses this template while handling tool results and
rendering `agent_response` into final user-facing text. Placeholders are
expanded in two stages:

1. Common NuCore module placeholders (for example `<<nucore_definitions>>`).
2. Runtime placeholders returned by `get_prompt_runtime_replacements(...)`.

## Session History

Session history is stored in `IntentRuntime.session_store`.

- Pass `session_id` to `handle_query` to enable history.
- History pruning uses `max_turns` from the active resolved profile.
- Oldest turns are evicted first.

## Directory Monitoring

Runtime supports hot-reload style monitoring:

- `subscribe_to_directory_changes(callback) -> int`
- `unsubscribe_from_directory_changes(subscriber_id) -> None`
- `start_directory_monitor(poll_interval_s=1.0) -> None`
- `stop_directory_monitor() -> None`
- `poll_directory_changes() -> tuple[DirectoryChangeEvent, ...] | None`

Core intent handlers are treated as immutable runtime assets and are not
hot-reloaded. Installed extension intents are monitored and can be reloaded.

## Extension Marketplace

Marketplace lifecycle logic is implemented inside the
`extension_marketplace_management` intent package. Runtime only monitors and
loads installed extension intents from the configured data directory.

Use the `extension_marketplace_management` intent actions via
`tool_extension_marketplace`:

- `discover`
- `list_installed`
- `install`
- `update`
- `uninstall`

Curated catalog location:

- `<path_to_data_directory>/extensions/catalog.json`

Default template source copied to the catalog at runtime startup:

- `src/intent_handler_directory/extension_marketplace_management/extension_catalog.json`

Minimal catalog shape:

```json
{
  "extensions": [
    {
      "id": "hebcal",
      "name": "Hebcal",
      "description": "Jewish calendar utilities",
      "git_url": "https://github.com/your-org/intent-hebcal.git",
      "ref": "main",
      "author": "Your Org",
      "author_url": "https://github.com/your-org"
    }
  ]
}
```

## Key Files

- `src/intent_handler/runtime.py`
- `src/intent_handler/router.py`
- `src/intent_handler/loader.py`
- `src/intent_handler/base.py`
- `src/intent_handler/runtime_assets/router/`
- `src/intent_handler/runtime_assets/nucore_runtime.example.json`
