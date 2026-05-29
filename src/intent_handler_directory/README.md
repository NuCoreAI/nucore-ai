# Intent Implementor Guide

This directory is for implementing runnable intents only.

Full infrastructure documentation is in:

- `src/intent_handler/README.md`

## What Belongs Here

Each runnable intent must have its own folder containing:

- `config.json`
- `prompt.md`
- `handler.py`

Only directories with `config.json` are discovered as intents.

## Minimal config.json

```json
{
  "intent": "my_intent",
  "description": "What this intent does",
  "handler": "handler.py"
}
```

## Optional config.json Fields

| Field | Description |
|---|---|
| `handler_class` | Explicit class name in `handler.py` |
| `routable` | Set to `false` to hide from the router. Default: `true` |
| `routing_examples` | Example queries used in router prompt generation |
| `router_hints` | Additional routing guidance for the router |
| `tool_files` | Optional list of tool JSON files (relative or absolute). Runtime also auto-discovers `tool_*.json` in this folder and merges both lists with de-duplication |
| `llm_config` | Per-intent overlay fields merged with runtime selection when no full intent profile exists |

## Routable Intents

Intents fall into two categories:

**Routable intents** (`routable: true`, the default) are advertised to the router LLM and can be selected directly based on user queries. Add `routing_examples` and `router_hints` to guide routing.

**Non-routable intents** (`routable: false`) are not advertised to the router and are not selected directly from user queries.

## Handler Requirements

Your handler must subclass `BaseIntentHandler` and implement:

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

How to use the new signature:

- `raw_response` is the response object produced by runtime via `call_llm`.
- `tool_calls` is the extracted tool-call list for the current post-processing step.
- Runtime now owns message building and LLM invocation.
- Handlers should only post-process: execute tool calls, validate/transform output,
  and attach tool results.

Recommended pattern:

```python
async def handle(..., raw_response=None, tool_calls=None):
  response = raw_response
  if response is None:
    return None

  calls = tool_calls if tool_calls is not None else response.get_tool_calls()
  for tool in calls:
    # execute backend action
    response.add_tool_result(...)

  return response
```

Recommended runtime prompt hook:

```python
def get_prompt_runtime_replacements(self, query, *, framework_context=None, route_result=None) -> dict[str, str]:
    return {}
```

The base class automatically:

- applies runtime placeholder replacements to `prompt.md`
- loads configured `tool_files` from config and merges them with auto-discovered `tool_*.json` files in the intent directory
- converts tools to the selected provider format
- normalizes returned provider tool calls to canonical Claude tool call format (`type=tool_use`, `id`, `name`, `input`)

## Prompt Placeholder Notes

- Shared common module placeholders are expanded before handler execution.
- Runtime placeholders are replaced from `get_prompt_runtime_replacements(...)`.
- Keys may be returned as raw names (`nucore_routines_runtime`) or full placeholders (`<<nucore_routines_runtime>>`).

## `framework_context`

An optional free-form string passed in by the **caller** of `handle_query`. When present it is appended to the user turn of the LLM message as a `# FRAMEWORK CONTEXT:` section before the user query. Use it to carry external state (session info, user preferences, system status) that the calling application knows about.

Example:

```python
result = await runtime.handle_query(
    "Turn on the patio lights",
    framework_context="User is authenticated. Location: home. Time zone: America/Los_Angeles.",
)
```

When `None` (the default) the section is omitted entirely.

## `extra_user_sections`

A `dict[str, str]` parameter on `build_messages` that lets a handler inject additional named sections into the user turn beyond `framework_context`. Each key becomes a section heading (uppercased) and each value becomes the body. Sections with empty or `None` values are skipped.

Example:

```python
messages = self.build_messages(
    query,
    framework_context=framework_context,
    route_result=route_result,
    extra_user_sections={
        "backend_snapshot": self.backend_api.get_status_snapshot(),
        "active_alerts": self.backend_api.get_alerts() or "",
    },
)
```

Resulting user turn order: `ROUTER RESULT` → `FRAMEWORK CONTEXT` → each extra section → `USER QUERY`.

## Session Management and Conversation History

Conversation history is managed globally per session (not per intent). Pass `session_id` to `handle_query` and the runtime will automatically include prior turns in each handler's message list.

```python
result = await runtime.handle_query(
    "Actually make them 50%",
    session_id="user-abc123",
)
```

Turns are stored as `(query, response)` pairs in a `ConversationHistory` object inside `IntentRuntime.session_store`. The history is prepended as alternating `user` / `assistant` messages before the current query, so the LLM sees the full conversation context.

**Pruning** is automatic: each history is capped at `max_turns` from the active resolved profile in `nucore_runtime` (default, router, or intent-specific). Oldest turns are dropped first.

```json
{
  "nucore_runtime": {
    "default": { "max_turns": 20 }
  }
}
```

To clear a session manually:

```python
runtime.session_store.clear("user-abc123")   # single session
runtime.session_store.clear_all()            # all sessions
```

In interactive CLI mode (`_run_loop`) history is enabled automatically with `session_id="default"`. Single-query mode (`--query`) is stateless (no `session_id`).

1. Create `src/intent_handler_directory/<intent_name>/`
2. Add `config.json` with `intent`, `description`, `handler`
3. Add `prompt.md`
4. Add `handler.py` with a single `BaseIntentHandler` subclass
5. Add tool files (`tool_*.json`) and optionally reference additional files via `tool_files`
6. Add `routing_examples` and `router_hints` for routing (skip for `routable: false` intents)

## Current Intent Inventory

### Routable (router-visible)

| Intent | Description |
|---|---|
| `command_control_status` | Device commands and real-time status |
| `routine_automation` | Create or edit routine logic |
| `routine_status_ops` | Enable/disable/run existing routines |
| `group_scene_ops` | Group and scene queries |

## Runtime Files (Do Not Treat as Intents)

Router and memory assets live outside this directory:

- `src/intent_handler/runtime_assets/nucore_runtime.example.json`
- `src/intent_handler/runtime_assets/router/`
- `src/intent_handler/runtime_assets/memory_store/`
