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

- `handler_class`: explicit class name in `handler.py`
- `previous_dependencies`: ordered list of dependency intents
- `routing_examples`: examples for router prompt generation
- `router_hints`: additional routing hints for router prompt generation
- `tool_files`: list of tool JSON files relative to this folder
- `llm_override`: optional per-intent override key into `src/intent_handler/runtime_assets/runtime_config.json` `supported_llms`
- `llm_config`: intent-local LLM defaults

## Handler Requirements

Your handler must subclass `BaseIntentHandler` and implement:

```python
async def handle(self, query, *, route_result=None, framework_context=None):
    ...
```

Recommended runtime prompt hook:

```python
def get_prompt_runtime_replacements(self, query, *, framework_context=None, route_result=None) -> dict[str, str]:
    return {}
```

The base class automatically:

- applies runtime placeholder replacements to `prompt.md`
- loads `tool_files` from config
- converts tools to the selected provider format

## Prompt Placeholder Notes

- Shared common module placeholders are expanded before handler execution.
- Runtime placeholders are replaced from `get_prompt_runtime_replacements(...)`.
- Keys may be returned as raw names (`nucore_routines_runtime`) or full placeholders (`<<nucore_routines_runtime>>`).

## Quick Add-Intent Checklist

1. Create `src/intent_handler_directory/<intent_name>/`
2. Add `config.json`
3. Add `prompt.md`
4. Add `handler.py`
5. Add tool files and reference them via `tool_files` (optional)
6. Add dependency and routing metadata as needed

## Runtime Files (Do Not Treat as Intents)

These are runtime-level files at this directory root:

- none required

Router and memory assets are not stored here. They live under:

- `src/intent_handler/runtime_assets/runtime_config.json`
- `src/intent_handler/runtime_assets/router`
- `src/intent_handler/runtime_assets/memory_store`
