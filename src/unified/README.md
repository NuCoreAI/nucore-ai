# Unified Runtime

The unified runtime is the only query-handling path in this repo: one system prompt (compact
`DEVICE DATABASE`/`ROUTINES DATABASE`), one native tool-calling agentic loop, no router, no
per-intent directory dispatch. It talks directly to the shared `NuCoreInterface`/`IoXWrapper`
backend.

## Layout

| File/dir | Purpose |
|---|---|
| `run_unified_runtime.py` | CLI entrypoint (`python -m unified.run_unified_runtime`) -- also runs as a native `wss://`-capable WebSocket server (`--websocket-port`/`--websocket-host`, TCP or a Unix domain socket, no HTTP framework involved), a lower-level alternative to the `eisy_ai` project's FastAPI-based chat server (sibling repo, depends on `nucore-ai`). In Unix socket mode, `--websocket-client-id` can require each connection's real peer UID (via `getpeereid()`) to match. |
| `runtime.py` | `UnifiedRuntime` -- builds the system prompt, runs the agentic loop, records conversation history. |
| `loop.py` | `AgenticLoop` -- the multi-turn tool-calling loop against an `LLMAdapter`. |
| `dispatch.py` | Tool name → handler dispatch table (`execute_tool`). |
| `fabrication_guard.py` | `detect_completion_claim` -- tool-agnostic regex heuristic flagging a reply that claims an action happened when no tool was called this turn; see "Fabrication guard" below. |
| `prompt_builder.py`, `prompt/` | Assembles the system prompt from `system_prompt.md`/`definitions.md` plus live `DEVICE DATABASE`/`ROUTINES DATABASE`, as three ordered, least-to-most-volatile sections (two `<<cache_boundary>>` markers in `system_prompt.md`) so each gets its own prompt-cache breakpoint -- see the module docstring for the exact split and why. |
| `tools/` | One `tool_<category>_<function>.json` file per tool (e.g. `tool_plugin_buy.json`), auto-discovered via a `tool_*.json` glob by `run_unified_runtime.py` -- the category prefix is a filenames-only convention for browsing/sorting, unrelated to each tool's own `"name"` field. |
| `handlers/` | One module per tool family, implementing the actual `NuCoreInterface`/`IoXWrapper` calls. |
| `routine_compiler/` | The DSL compiler `create_or_update_routine` uses to turn `if`/`then`/`else` Python-like source into NuCore's `Trigger` schema. |
| `adapters/` | Per-provider `LLMAdapter` implementations (Claude, OpenAI, Gemini, Grok, llama.cpp). |
| `models.py` | `IntentHandlerResult` (the return type `handle_query` produces), `ConversationTurn`/`ConversationHistory`. |
| `session_store.py` | In-memory `session_id → ConversationHistory` map, plus `lock(session_id)` (one `asyncio.Lock` per session id). Shared across every WebSocket connection by `run_unified_runtime._run_websocket_server` so a reconnect finds its history instead of starting empty; the lock is what keeps that sharing safe against two concurrent requests for the same session -- see "Session history across connections" below. |
| `stream_handler.py` | `StreamHandler` -- streams live tokens to a connected websocket (`--websocket-port`/`--websocket-host` mode); a fresh `runtime_config` is built per connection since it bakes in a bound `stream_handler.handle_stream_chunk` callback (see `_run_websocket_server`'s docstring). |
| `dispatch_builder.py`, `provider_dispatch_adapter.py`, `provider_clients.py`, `runtime_config.py` | Runtime-profile JSON loading and per-provider `LLMAdapter` construction, shared by both process entrypoints. |
| `history_compaction.py` | `maybe_compact_history` -- collapses the oldest half of a session's conversation history into one LLM-generated summary turn once its estimated token size exceeds `history_token_budget` (runtime config, default 20000); falls back to plain truncation if the summarization call itself fails. |
| `runtime_config.example.json`, `runtime_config.openai.example.json`, `runtime_config.grok.example.json` | Example runtime profiles, one per provider (see below). |

## Running it

Create a runtime profile JSON first (see `runtime_config.example.json` for the format: a
`nucore_runtime.default` block, optionally a `nucore_runtime.unified` block for a dedicated
model/temperature just for this path). `runtime_config.openai.example.json`/
`runtime_config.grok.example.json` are ready-to-copy alternatives for those providers -- see the
top-level `README.md`'s "Supported Providers" section.

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

1. Add `tools/tool_<category>_<function>.json` (`name`, `description`, `input_schema` -- Claude
   tool-authoring format; `name` itself stays whatever reads best, e.g. `buy_plugin` in
   `tool_plugin_buy.json` -- the filename's category prefix is just for grouping related tools on
   disk). It's auto-discovered by `run_unified_runtime.py`'s `tool_*.json` glob, no registration
   needed there.
2. Implement `async def <name>(nucore_interface: NuCoreInterface, args: dict) -> Any` in the
   relevant `handlers/*.py` module (new or existing).
3. Register it in `dispatch.py`'s `TOOL_HANDLERS` dict.
4. Add tests under `tests/unified/handlers/`.

`create_or_update_routine` is the one tool whose grammar documentation lives entirely in its own
tool JSON's `description` rather than in `prompt/definitions.md`, since it's large and specific to
that one tool.

## Fabrication guard

A code-level backstop for a recurring production pattern: the model states an action completed
("Done!", "I've turned off...", "is now set to...") in a turn where it called **no tool at all**.
`fabrication_guard.py`'s `detect_completion_claim(text)` is a tool-agnostic regex heuristic over
the reply text; `AgenticLoop.run` (`loop.py`) only calls it when zero tool calls happened that
turn -- that structural precondition, not the pattern list, is what makes this cover every tool
(present and future) with no per-tool maintenance. It does **not** catch a *wrong* tool being
called while still claiming the right thing happened (e.g. a status check standing in for a
repeat command) -- a known v1 gap; closing it would need matching claim-type to expected-tool,
reintroducing the per-tool coupling this design avoids.

Controlled by two top-level `runtime_config` keys (no CLI flag -- see
`runtime_config.example.json`):

| Key | Default | Effect |
|---|---|---|
| `fabrication_guard_mode` | `"log"` | `"off"` disables detection. `"log"` flags a match to the prompt log (`PromptLogManager.write_flag`, kind `"fabrication_flag"`) without changing the reply -- observation only. `"block"` retries the turn once and, if the retry also fabricates, replaces the reply with a fixed "I'm not fully sure that completed correctly -- please check, or ask me to try again." |
| `max_fabrication_retries` | `1` | Retry budget used only in `"block"` mode. |

The matching prompt-side half is `system_prompt.md`'s `# CRITICAL RULES` section -- originally
five separate, topic-specific "Self-check before every reply" blocks, first collapsed into one
statement with short pointer bullets left in each original location, then trimmed further to
three one-line rules (tool-mediated claims, per-request freshness, per-item batch reporting) once
live production logs showed the verbose version wasn't measurably reducing fabrication rate
anyway -- the code-level guard above is the actual backstop either way, which is what made
cutting the prompt-side prose low-risk.

## Session history across connections

`run_unified_runtime.py`'s `--websocket-port`/`--websocket-host` server mode shares one
`SessionStore` across every accepted connection (built once in `_run_websocket_server`, passed
into each connection's `UnifiedRuntime(session_store=...)`), keyed by the durable session id
`EisyUIContext.get_user_id()` establishes per logical client. This is what lets a reconnect
(network blip, page reload, the bridge process itself reconnecting) find its prior conversation
history instead of starting over empty -- previously each connection got its own private, empty
`SessionStore`, so a reconnect silently lost everything despite the session id staying stable
across it.

Sharing one store safely requires serializing same-session access: `UnifiedRuntime.handle_query`
holds `session_store.lock(session_id)` for its entire read-history -> generate -> append-history
sequence, so a second concurrent request for the same session id waits for the first to fully
finish rather than racing it on the same `ConversationHistory` object. Every other caller
(`--query`/REPL mode, and any test constructing `UnifiedRuntime` without a `session_store` kwarg)
is unaffected -- it defaults to a private, unshared store, so the lock is uncontended and a no-op
in practice.
