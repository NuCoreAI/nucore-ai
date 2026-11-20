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
| `prompt_builder.py`, `prompt/` | Assembles the system prompt from `system_prompt.md` (one section per concept -- Devices, Groups and scenes, Folders, Variables, Routines, Plugins -- plus general rules) plus live `DEVICE DATABASE`/`ROUTINES DATABASE`, as three ordered, least-to-most-volatile sections (two `<<cache_boundary>>` markers in `system_prompt.md`) so each gets its own prompt-cache breakpoint -- see the module docstring for the exact split and why. |
| `tools/` | One `tool_<category>_<function>.json` file per tool (e.g. `tool_plugin_buy.json`), auto-discovered via a `tool_*.json` glob by `run_unified_runtime.py` -- the category prefix is a filenames-only convention for browsing/sorting, unrelated to each tool's own `"name"` field. |
| `handlers/` | One module per tool family, implementing the actual `NuCoreInterface`/`IoXWrapper` calls. |
| `routine_compiler/` | The DSL compiler `create_or_update_routine` uses to turn `if`/`then`/`else` Python-like source into NuCore's `Trigger` schema. |
| `adapters/` | Per-provider `LLMAdapter` implementations (Claude, OpenAI `/v1/chat/completions`, OpenAI `/v1/responses` via `OpenAIResponsesAdapter`, Gemini, Grok, llama.cpp). |
| `models.py` | `IntentHandlerResult` (the return type `handle_query` produces), `ConversationTurn`/`ConversationHistory`. |
| `session_store.py` | In-memory `session_id → ConversationHistory` map, plus `lock(session_id)` (one `asyncio.Lock` per session id). Shared across every WebSocket connection by `run_unified_runtime._run_websocket_server` so a reconnect finds its history instead of starting empty; the lock is what keeps that sharing safe against two concurrent requests for the same session -- see "Session history across connections" below. Optional `persist_dir` adds disk persistence and multi-session management per user -- see "Persisted sessions" below. |
| `session_persistence.py` | `SessionPersistence` -- the synchronous, on-disk half of persisted sessions (one directory per user, named by a hash of `user_id`; atomic writes; secret redaction before write). Called by `session_store.py` via `asyncio.to_thread`. |
| `plugin_output_root_migration.py` | `migrate_plugin_output_root` -- automatic, prompt-free relocation of `--plugin-output-root`'s contents to a new directory when `--old-plugin-output-root` is given and still exists, run inline from `run_unified_runtime.py main()`; corrects each plugin's `server_entry.json` path, reinstalls any previously-installed plugin into its original slot (regenerating its rc.d service file) with its exact run state restored, and renames the old directory to `<old_root>.old` only on full success. |
| `stream_handler.py` | `StreamHandler` -- streams live tokens to a connected websocket (`--websocket-port`/`--websocket-host` mode); a fresh `runtime_config` is built per connection since it bakes in a bound `stream_handler.handle_stream_chunk` callback (see `_run_websocket_server`'s docstring). |
| `dispatch_builder.py`, `provider_dispatch_adapter.py`, `provider_clients.py`, `runtime_config.py` | Runtime-profile JSON loading and per-provider `LLMAdapter` construction, shared by both process entrypoints. |
| `history_compaction.py` | `maybe_compact_history` -- once a session's estimated token size exceeds `history_token_budget` (runtime config, default 20000) the history is compacted. On Claude the live request is forked (same system prompt and tools, so the cached history is read cheaply, with a final summarize instruction and `tool_choice` none) and the whole history is replaced by that one summary; on any other provider, or if the fork fails, the oldest turns (all but the newest ~half-budget) are summarized from their text and short tool-call excerpts into one pseudo-turn, with plain truncation as the last resort. Each compaction is recorded for `/session-stats`. |
| `usage_stats.py` | Per-session token accounting and the `/session-stats` report: per-provider usage normalization, running totals and recent-request rows kept on `ConversationHistory.usage`, compaction and mid-turn-over-budget events, component estimates and report formatting. |
| `runtime_config.example.json`, `runtime_config.openai.example.json`, `runtime_config.openai-responses.example.json`, `runtime_config.grok.example.json` | Example runtime profiles, one per provider (see below). |

## Running it

Create a runtime profile JSON first (see `runtime_config.example.json` for the format: a
`nucore_runtime.default` block, optionally a `nucore_runtime.unified` block for a dedicated
model/temperature just for this path). `runtime_config.openai.example.json`/
`runtime_config.openai-responses.example.json`/`runtime_config.grok.example.json` are
ready-to-copy alternatives for those providers -- see the top-level `README.md`'s "Supported
Providers" section. The `openai-responses` variant is for reasoning-tier OpenAI models that
reject function tools on `/v1/chat/completions` under every `reasoning_effort` setting (see
`design/migration-to-openai-response-api.md`) -- same account/API key, just a different
endpoint and a native `web_search` tool for the `unified` tool set. The default
`runtime_config.example.json` (Claude) profile already gets this same native search for
`unified` with no extra config either -- see the top-level `README.md`'s web search paragraph.

```shell
python -m unified.run_unified_runtime \
  --runtime-config src/unified/runtime_config.example.json \
  --backend-api-classpath iox.IoXWrapper \
  --backend-api-base-url https://192.168.6.134 \
  --backend-api-username admin \
  --backend-api-password yourpassword
```

This drops into an interactive REPL (one query per line). See the top-level `README.md` for the
full CLI flag reference, secrets-file format, and logging flags -- they're identical for this
entrypoint.

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
tool JSON's `description` rather than in `system_prompt.md`'s ROUTINES section, since it's large
and specific to that one tool.

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

The matching prompt-side half is `system_prompt.md`'s `# CRITICAL RULES` section, at the very top
of the prompt -- originally five separate, topic-specific "Self-check before every reply" blocks,
then collapsed and trimmed repeatedly once live production logs showed the verbose version wasn't
measurably reducing fabrication rate anyway. Today it is a short list of general rules (call a tool
whenever one applies, no unverified facts or capability claims, no invented ids, act on tool errors
rather than guessing, re-check on pushback), echoed by a two-line `# REMINDER` at the very end of
the prompt -- the code-level guard above is the actual backstop either way, which is what made
cutting the prompt-side prose low-risk.

On a `"block"`-mode retry, `AgenticLoop.run` also forces `tool_choice: {"type": "any"}` for that
one retry round, but **only when the active provider is Claude** (resolved via
`_effective_provider`, which goes through `ProviderDispatchLLMAdapter.get_adapter_for_provider`
when the loop is wired to the dispatcher, since production doesn't wire it directly to
`ClaudeAdapter`). This sidesteps forcing a tool call before knowing whether the customer's query
even needs one: the guard only escalates *after* a round has already fabricated with zero tool
calls, so by the retry the need for a tool call is no longer a guess. `ClaudeAdapter.generate`
forwards `tool_choice` from the round's `llm_config` when present and always returns the response's
`stop_reason`, which `AgenticLoop.run` logs each round (`unified: round %d stop_reason=%s`) as a
diagnostic signal independent of the fabrication-claim regex. Other providers' adapters are
untouched -- `force_tool_choice` is computed but never set to `True` off a non-Claude retry.

## Session history across connections

`run_unified_runtime.py`'s `--websocket-port`/`--websocket-host` server mode shares one
`SessionStore` across every accepted connection (built once in `_run_websocket_server`, passed
into each connection's `UnifiedRuntime(session_store=...)`), keyed by the durable session id
`EisyUIContext.get_user_id()` establishes per logical client -- `user_id` **alone**. An earlier
version combined it with `client_id` specifically so the same logged-in user on two different
browsers/machines would land in different sessions; that was reversed after confirming (by direct
testing) that `client_id` is regenerated on every browser refresh, so keying by it meant a plain
page reload already lost history -- worse than the device-merging tradeoff it was meant to avoid.
This is what lets a reconnect (network blip, page reload, the bridge process itself reconnecting)
find its prior conversation history instead of starting over empty -- previously each connection
got its own private, empty `SessionStore`, so a reconnect silently lost everything despite the
session id staying stable across it.

The same shared `SessionStore` also holds each identity's last-known **UI context** (the
customer's current screen, via `get_ui_context`/`set_ui_context`), keyed by identity (`user_id`)
alone -- deliberately *not* suffixed by tool set, since the customer's screen doesn't change just
because the chatbot switched between `unified`/`plugin_authoring`. `EisyUIContext.get_context()` reads
through to this shared slot whenever *this* connection hasn't received a context message of its
own yet, so a brand-new connection (a reconnect -- see `EisyUIContext`'s own docstring on why it's
per-connection, not global, and why that's still correct) doesn't look like the customer's screen
has become unknown just because the object holding it was. UI context is never persisted to disk
(see "Persisted sessions" below) -- it's volatile, connection-local screen state.

Sharing one store safely requires serializing same-session access: `UnifiedRuntime.handle_query`
holds `session_store.lock(session_id)` for its entire read-history -> generate -> append-history
sequence, so a second concurrent request for the same session id waits for the first to fully
finish rather than racing it on the same `ConversationHistory` object. UI context reads/writes are
unlocked plain dict operations -- a single dict-key assignment never interleaves under asyncio's
cooperative scheduling, so there's nothing a lock would protect there. Every other caller
(REPL mode, and any test constructing `UnifiedRuntime` without a `session_store` kwarg) is
unaffected -- it defaults to a private, unshared store, so the lock is uncontended and a no-op in
practice.

## Persisted sessions

`SessionStore(persist_dir=...)` (`--session-persist-dir`) adds disk persistence on top of
everything above: a user's conversation history and last-active mode survive a full process
restart, not just an in-process reconnect. `None` (the default) is a complete no-op -- zero new
disk I/O, identical behavior to before this existed.

Within each mode, a user can hold multiple named sessions (not just one continuously-growing
history), managed by eight chat commands recognized the same way `/developer`/`/customer` are
(`EisyUIContext._apply_switch_command_or_return`, both the websocket-JSON and plain-REPL-string
input paths): `/new-session`, `/sessions`, `/load-session <id>`, `/rename-session <id> <name>`,
`/delete-session <id>`, `/delete-all-sessions`, `/compact-session`, `/session-stats`. Unlike the two mode-switch
commands, these are answered asynchronously from `_run_once` (`_handle_session_command`) rather
than at recognition time, since a session lookup/mutation is async disk I/O.

See `session_persistence.py` (the synchronous, on-disk half -- one directory per user named by a
hash of `user_id`, atomic writes, secret redaction before write) and `session_store.py` (the
async, in-memory-cached half that makes concurrent session-management commands for the same user
safe) for the implementation, and `design/developers/merged-toolsets.md`'s "Persisted,
multi-session history" section for the full design.

## History-budget notices and manual compaction

`history_compaction.maybe_compact_history` (called once per turn from `UnifiedRuntime.handle_query`)
compacts a session once its estimated token size exceeds that tool set's `history_token_budget`.

- **On Claude (fork):** `AgenticLoop.summarize` resends the turn's own request -- same system
  sections, same history, same exported tools, `cache_messages`, `tool_choice` none, no streaming --
  with one final user instruction asking for a summary (requests and intent, decisions, facts the
  tools showed, what was done and what wasn't, current state, the pending step). Because the prefix
  matches the previous request, it reads the already-cached history at the cache-read price and the
  model sees every tool result. On success the **entire** history is replaced by that one summary
  turn. `handle_query` therefore builds the system prompt and loop *before* compaction on Claude
  profiles and reuses them for the turn (`_prepare_fork_summarizer`).
- **Elsewhere, or if the fork fails (standalone):** the oldest turns (everything except the newest
  ~half-budget) are summarized by a separate call from their text plus short tool-call/result
  excerpts, keeping the newest turns verbatim; if that call fails too, the oldest turns are dropped.
- **Notices:** its `notify` parameter -- wired to `UnifiedRuntime._notify_history_budget`, which
  sends over whichever channel is attached (websocket JSON frame or REPL stdout) -- emits one
  italic Markdown status line the first time estimated usage crosses each 10%-of-budget decile at or
  above 50% (tracked on `ConversationHistory.last_budget_notice_decile`, deliberately never restored
  from disk -- see that field's own docstring), then a distinct "history was compacted" line for
  whichever turn actually crosses 100% and triggers real compaction, resetting the tracked decile
  back to 0. Both notices end with a blank line because the UI appends the reply stream to the
  notice's chat bubble.

`/compact-session` is the manual counterpart (`UnifiedRuntime.compact_session`, which also receives
the connection context so the fork can rebuild the same tools), forcing the same compaction early
even while under budget (`maybe_compact_history`'s `force=True`), under the same
per-`(user_id, tool_set)` lock `handle_query` holds for its own automatic check.

Compaction is checked only at the start of a turn. A single long turn (dozens of tool rounds) can
therefore grow far past the budget before the next check; `AgenticLoop.run` watches for that, see
the log-only mid-turn detection below.

### Prompt caching on Claude

`ClaudeAdapter` puts `cache_control` with a fixed `ttl: "1h"` (`_CACHE_TTL`) on the tools, on the
system blocks that fit under the API's 4-breakpoint cap, and on the end of the conversation, so a
pause of up to an hour between turns still reads the cached prefix instead of rewriting it (a 1h
write costs 2x input instead of 1.25x; reads cost 0.1x). There is no `cache_ttl` setting; a
leftover one in an old config is ignored. The plugin-authoring system prompt is deliberately a
single static block: anything that changes between turns and sits before the messages (it once held
an installed-plugins list) invalidates the cached conversation after it, so the model calls
`list_installed_plugins` for live state instead.

## Usage accounting and `/session-stats`

`/session-stats` (`UnifiedRuntime.session_stats`, `usage_stats.py`) replies with a Markdown report
for the current session of the current mode. It is answered locally from `_run_once`
(`_handle_session_command`) like the other session commands -- so it requires `--session-persist-dir`
and a signed-in user -- and makes **no model call**; nothing it prints enters the conversation
history.

**Recording.** `AgenticLoop.run` and `summarize` report each request's usage to an `on_usage(kind,
usage)` callback (kinds `round` and `compaction fork`; the standalone summarizer is not recorded).
`UnifiedRuntime` hands it a closure over the session's `ConversationHistory.usage` dict, which
`usage_stats.record_request` updates: `normalize_usage` maps each provider's shape (Claude's
`input_tokens`/`cache_*`, OpenAI chat `prompt_tokens`, OpenAI Responses `input_tokens`, Gemini
`usageMetadata`) to `input_total`, `cache_read`, `cache_write_5m`, `cache_write_1h` and `output`;
unknown shapes count as zero and nothing here raises. The dict holds running totals, a dollar total
for models in the small price table, the last 20 requests, the last 20 compaction events
(`fork`/`summary`/`truncated`, tokens before and after, recorded by `maybe_compact_history`) and the
mid-turn counter. It is saved and restored with the session by `session_persistence`
(`history_to_dict`/`history_from_dict`), so it does not depend on the prompt log (a debug option).

**Composition.** To split the next request, `session_stats` rebuilds the same system sections and
loop a real turn would (`system_prompt_builder`, `_build_loop`; if that fails -- no connection
context -- the report still renders with empty system/tools rows) and
`usage_stats.estimate_composition` sizes each system section (labelled by the markers it contains:
DEVICE DATABASE, ROUTINES DATABASE, TIME & LOCATION, USER PREFERENCES), the exported tools, and
history split into the compacted summary, conversation text and tool traffic (messages containing
`tool_use`/`tool_result`/server-tool blocks), using `history_compaction.estimate_tokens`
(`chars / 3`). When the newest recorded `round` request is still comparable (no compaction recorded
at or after it, and the real/estimated ratio is within 0.5-2.0) every component is multiplied by
`last request input_total / estimated total`, so the table sums to a measured number and the
heading says "calibrated"; otherwise the raw estimates are shown and labelled "estimated".

**Window and price.** The context window comes from the profile's optional `context_window`
(validated in `runtime_config.py`, see `runtime_config.md`) or the built-in `_WINDOWS` table;
dollars come from `_PRICES` (cache reads 0.1x, 5m writes 1.25x, 1h writes 2x input). A model in
neither table just omits the window share or the cost.

**Report contents:** context for the next request vs window; history vs `history_token_budget`;
`max_tokens` reserve; the parts table; the last request (read / written / uncached, hit rate);
session totals; compaction count and latest before/after; mid-turn events; the last five requests.
User-facing explanation: `../../../eisy-ai/USER_GUIDE.md` ("Seeing how full a session is").

**Mid-turn over-budget detection (log only).** Before each round after the first,
`AgenticLoop.run` estimates the conversation so far (history plus this turn's messages, system
sections excluded) and, if it exceeds `mid_turn_budget` (the runtime passes `2 *
history_token_budget`), calls `on_mid_turn_over_budget(round, tokens, budget)`; it fires again only
at the next multiple of the budget. `UnifiedRuntime._note_mid_turn_over_budget` logs a
`would compact mid-turn at round N` warning, writes a `mid_turn_over_budget` prompt-log line and
counts the event for `/session-stats`. Nothing is compacted or altered; the data is for deciding
whether real mid-turn compaction is worth building. Both hooks are best-effort -- an exception is
logged and never breaks the turn.

## Secret redaction in logs

Two layers keep API keys and tokens out of log output (`src/utils/secret_redaction.py`,
`src/utils/logger.py`):

- **Registered secrets** -- `register_secret(value)` (provider API keys as clients are built,
  OAuth client secrets, MCP tokens) and `redact()`, applied by `RedactingFilter` on every handler
  `configure_logging` builds, by the prompt log, and by session persistence before write.
- **Pattern scrubbing** -- `scrub()` = `redact()` plus recognition by shape (`sk-ant-...`,
  `sk-...`, `xai-...`, `gsk_...`, `AIza...`, `Bearer <token>`) and by quoted value after a sensitive
  field name (`api_key`, `client_secret`, `access_token`, `refresh_token`, `password`, `secret`,
  `authorization`, including the backslash-escaped JSON a logger shows for a JSON string inside a
  string) or URL parameter. It catches secrets that were never registered or are logged before
  registration. It is applied only to log records, never to persisted history: `redact()` stays
  registry-only there because rewriting code-like text (`api_key = "..."` in generated plugin
  code) would corrupt the stored conversation.

`install_redacting_record_factory()` (run when `utils.logger` is imported, and again by
`configure_logging`) applies `scrub()` as a `logging` record factory, upstream of every logger and
handler in the process. That is what covers `debug.log`: it is written by udi_interface's own
`PolyLogger` handler, which logs each Polyglot message it receives -- including the plugin's custom
parameters, provider API keys and all -- and which `RedactingFilter` can't reach. Lines written
before this existed stay in old logs; rotate any key that appeared in one. Polyglot's own stored
custom parameters (database and UI) are unaffected by any of this.
