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
| `routable` | Set to `false` to hide from the router (dependency-only intents). Default: `true` |
| `previous_dependencies` | Ordered list of intent names that must run before this one |
| `routing_examples` | Example queries used in router prompt generation |
| `router_hints` | Additional routing guidance for the router LLM |
| `tool_files` | Optional list of tool JSON files (relative or absolute). Runtime also auto-discovers `tool_*.json` in this folder and merges both lists with de-duplication |
| `llm_override` | Key into `runtime_config.supported_llms` to use a specific LLM for this intent |
| `llm_config` | Per-intent LLM call defaults merged with runtime selection |

## Routable vs Pipeline Intents

Intents fall into two categories:

**Routable intents** (`routable: true`, the default) are advertised to the router LLM and can be selected directly based on user queries. Add `routing_examples` and `router_hints` to guide routing.

**Pipeline filter intents** (`routable: false`) are never shown to the router. They only run when listed in another intent's `previous_dependencies`. Use this pattern for pre-processing steps that select candidates (devices, routines, etc.) for a downstream execution intent.

Example — device-action pipeline:

```
router selects: command_control_status
  └── previous_dependencies: [device_filter]   ← runs first, routable: false
```

## Handler Requirements

Your handler must subclass `BaseIntentHandler` and implement:

```python
async def handle(self, query, *, route_result=None, framework_context=None, dependency_outputs=None):
    ...
```

Recommended runtime prompt hook:

```python
def get_prompt_runtime_replacements(self, query, *, dependency_outputs: IntentHandlerResult | str | dict[str, Any] | None = None, framework_context=None, route_result=None) -> dict[str, str]:
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

## Quick Add-Intent Checklist

1. Create `src/intent_handler_directory/<intent_name>/`
2. Add `config.json` with `intent`, `description`, `handler`
3. Add `prompt.md`
4. Add `handler.py` with a single `BaseIntentHandler` subclass
5. Add tool files (`tool_*.json`) and optionally reference additional files via `tool_files`
6. Add `routing_examples` and `router_hints` for routing (skip for `routable: false` intents)
7. Add `previous_dependencies` if this intent requires upstream processing

## Current Intent Inventory

### Routable (router-visible)

| Intent | Description | Depends on |
|---|---|---|
| `command_control_status` | Device commands and real-time status | `device_filter` |
| `routine_automation` | Create or edit routine logic | `device_filter` |
| `routine_status_ops` | Enable/disable/run existing routines | `routine_filter` |
| `group_scene_operations` | Group and scene queries | `device_filter` |
| `general_help` | Conceptual help, definitions, non-execution queries | — |

### Pipeline filters (`routable: false`)

| Intent | Description | Used by |
|---|---|---|
| `device_filter` | Narrows device candidates for downstream execution | `command_control_status`, `routine_automation`, `group_scene_operations` |
| `routine_filter` | Narrows routine candidates for downstream execution | `routine_status_ops` |

## Runtime Files (Do Not Treat as Intents)

Router and memory assets live outside this directory:

- `src/intent_handler/runtime_assets/runtime_config.json`
- `src/intent_handler/runtime_assets/router/`
- `src/intent_handler/runtime_assets/memory_store/`
