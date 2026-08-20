# Unified Runtime

The unified runtime is the only query-handling path in this repo: one system prompt (compact
`DEVICE DATABASE`/`ROUTINES DATABASE`), one native tool-calling agentic loop, no router, no
per-intent directory dispatch. It talks directly to the shared `NuCoreInterface`/`IoXWrapper`
backend.

## Layout

| File/dir | Purpose |
|---|---|
| `run_unified_runtime.py` | CLI entrypoint (`python -m unified.run_unified_runtime`) -- also reused by the `nucore_assistant` project's WebSocket chat server (sibling repo, depends on `nucore-ai`). |
| `runtime.py` | `UnifiedRuntime` -- builds the system prompt, runs the agentic loop, records conversation history. |
| `loop.py` | `AgenticLoop` -- the multi-turn tool-calling loop against an `LLMAdapter`. |
| `dispatch.py` | Tool name → handler dispatch table (`execute_tool`). |
| `prompt_builder.py`, `prompt/` | Assembles the system prompt from `system_prompt.md`/`definitions.md` plus live `DEVICE DATABASE`/`ROUTINES DATABASE`. |
| `tools/` | One `tool_*.json` file per tool, auto-discovered by `run_unified_runtime.py`. |
| `handlers/` | One module per tool family, implementing the actual `NuCoreInterface`/`IoXWrapper` calls. |
| `routine_compiler/` | The DSL compiler `create_or_update_routine` uses to turn `if`/`then`/`else` Python-like source into NuCore's `Trigger` schema. |
| `adapters/` | Per-provider `LLMAdapter` implementations (Claude, OpenAI, Gemini, Grok, llama.cpp). |
| `models.py` | `IntentHandlerResult` (the return type `handle_query` produces), `ConversationTurn`/`ConversationHistory`. |
| `session_store.py` | In-memory `session_id → ConversationHistory` map. |
| `stream_handler.py` | `StreamHandler` base class (the runtime doesn't stream yet -- present for the CLI's result-printing path and future use). |
| `dispatch_builder.py`, `provider_dispatch_adapter.py`, `provider_clients.py`, `runtime_config.py` | Runtime-profile JSON loading and per-provider `LLMAdapter` construction, shared by both process entrypoints. |
| `runtime_config.example.json` | Example runtime profile (see below). |

## Running it

Create a runtime profile JSON first (see `runtime_config.example.json` for the format: a
`nucore_runtime.default` block, optionally a `nucore_runtime.unified` block for a dedicated
model/temperature just for this path).

```shell
python -m unified.run_unified_runtime \
  --runtime-config src/unified/runtime_config.example.json \
  --backend-api-classpath iox.IoXWrapper \
  --backend-api-base-url https://192.168.6.134 \
  --backend-api-username admin \
  --backend-api-password yourpassword \
  --query "Turn on the patio lights"
```

Omit `--query` for an interactive REPL. See the top-level `README.md` for the full CLI flag
reference, secrets-file format, and logging flags -- they're identical for this entrypoint.

## Adding a new tool

1. Add `tools/tool_<name>.json` (`name`, `description`, `input_schema` -- Claude tool-authoring
   format). It's auto-discovered by `run_unified_runtime.py`'s `tool_*.json` glob, no registration
   needed there.
2. Implement `async def <name>(nucore_interface: NuCoreInterface, args: dict) -> Any` in the
   relevant `handlers/*.py` module (new or existing).
3. Register it in `dispatch.py`'s `TOOL_HANDLERS` dict.
4. Add tests under `tests/unified/handlers/`.

`create_or_update_routine` is the one tool whose grammar documentation lives entirely in its own
tool JSON's `description` rather than in `prompt/definitions.md`, since it's large and specific to
that one tool.
