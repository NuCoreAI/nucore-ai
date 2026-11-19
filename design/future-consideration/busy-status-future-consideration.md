# Per-tool status through the "AI busy" animation, with random words during long silences

**Note on this document:** this is a saved plan, not built. It was written on 2026-11-18 and
deliberately set aside for now. Nothing here has been implemented.

## Context

Plugin authoring used to feel chatty. Since commit `2751315` (Nov 4) the per-round
"*Thinking...*" chat message is gone, replaced by one turn-level busy animation
(`nucore_interface.notify_ai_busy`). Large code writes then run for 40-75 seconds with
nothing visible, because tool-call arguments aren't streamed to the chat, only text.

Desired behavior: per-tool status lines, delivered by *updating the text of the existing busy
animation* rather than by chat messages. During long silences the animation should cycle
through random words.

## Design

**1. New `src/unified/busy_status.py`** (the only place that talks to `notify_ai_busy`):

- `describe_tool(name) -> str`: a short status phrase. A small verb map (`get` -> "Getting",
  `lookup` -> "Looking up", `validate` -> "Validating", `generate` -> "Generating",
  `run` -> "Running", `search` -> "Searching", `install` -> "Installing", `setup` -> "Setting up",
  `switch` -> "Switching", ...) plus the rest of the tool name as words:
  `validate_profile` -> "Validating profile", `generate_plugin_scaffold` ->
  "Generating plugin scaffold". A small override dict for names that read badly: `node_op`,
  `plugin_ops`, `variable_op`, `preference_op`, `routine_status_op`, `group_scene_op`,
  `multi_device_scene`, `scene_test`, `mdns_search` ("Searching the network"),
  `search_web`/`web_search` ("Searching the web"), `run_shell_command`
  ("Running a shell command"). Fallback for anything else: `"Running <name with spaces>"`.
  No trailing ellipsis -- the existing random words ("Thinking") have none and the UI animates
  them.
- `class BusyStatus` (one per turn):
  - `start()` sends `enabled=True` with a random word and starts a ticker task.
  - `on_tool(phase, name)`: `"preparing"` (the model starting to write the call) and
    `"running"` (the tool executing) send `describe_tool(name)`, only when the text changed.
    `"done"` immediately sends a fresh random word, so the last tool's text never goes stale
    while the model composes its next call.
  - **Ticker:** every `_IDLE_INTERVAL` (7 s), if no update was sent in that time and no tool is
    currently executing, send a different random word. While a tool runs its status stays;
    random words are only for model-generation silence.
  - `stop()` cancels the ticker and sends `enabled=False` exactly once (idempotent).
  - All sends go through one `asyncio.Lock`, so a ticker word can never overwrite a newer tool
    status out of order.
  - Failure handling: `NotImplementedError` (backend without `notify_ai_busy`) disables all
    further updates quietly after one warning. Any other exception logs one warning and carries
    on. `stop()` is always attempted so the animation can't get stuck on.
- `_BUSY_STATUS_WORDS` moves here from `runtime.py`.

**2. `adapters/claude_adapter.py`: early signal while the model is still writing a tool call.**
`_stream_once` iterates the SDK's stream events instead of `text_stream`: `content_block_delta`
with a `text_delta` still forwards text exactly as before, and `content_block_start` with a
`tool_use` or `server_tool_use` block calls `config["on_tool_use_start"](name)` (best-effort).
This fills the real silence: the status switches to "Generating plugin scaffold" the moment the
model begins that call, instead of after a 70-second write. Other providers ignore the config
key and get only the `running` phase.

**3. `loop.py`: one new optional hook** `on_tool_status(phase, name)`:

- The loop passes `round_config["on_tool_use_start"]` -> `"preparing"`.
- It calls `"running"` just before `await self.dispatch(...)` and `"done"` after, for each tool
  call.
- Best-effort: exceptions are logged and swallowed, like `on_raw_response`.

**4. `runtime.py`:** `handle_query` creates a `BusyStatus` over
`self.nucore_interface.notify_ai_busy`, awaits `start()` where `_notify_busy_start` is called
today, passes `busy.on_tool` into `_build_loop` -> `AgenticLoop(on_tool_status=...)`, and awaits
`stop()` in the existing `finally`. A mid-turn tool-set switch rebuilds the loop with the same
`BusyStatus`. `_notify_busy_start`/`_notify_busy_end` are removed.

**Known limits.**

- The animation is global to eisy-ui. Overlapping turns from different connections already
  share it, so last writer wins.
- Gemini and OpenAI get statuses at tool execution only, because there is no early signal.

## Open risk (verify live)

eisy-ui's source wasn't available when this was planned. The design assumes a repeated
`POST /api/ai/busy {"enabled": true, "message": ...}` updates the text of the running animation.
If the UI ignores or restarts on repeated `enabled: true`, the UI side needs adjusting; the
design already sends an update only when the text changes.

## Tests

- `tests/unified/test_busy_status.py` (new): `describe_tool` for verb-map, override and fallback
  names; `start`/`stop` send exactly one `enabled=True` and one `enabled=False`; tool phases
  update the text only when it changes and `done` sends a random word; the ticker emits random
  words after the idle interval (injectable sleep) but not while a tool is running, and never
  repeats a word twice in a row; `NotImplementedError` disables updates after one warning, and
  other errors don't stop `stop()`.
- `tests/unified/test_loop.py`: `on_tool_status` gets `running`/`done` in order around each
  dispatch, `preparing` is wired through `round_config`, and a raising hook doesn't break the
  turn.
- `tests/unified/adapters/test_claude_adapter.py`: a stream with a `tool_use` start event calls
  the hook with the tool name, text deltas still reach the stream handler, and `_FakeStream`
  gains `__aiter__`.
- `tests/unified/test_runtime.py`: the two existing busy tests are updated to the new mechanism
  (start/end around the turn, end exactly once on exception, missing or raising
  `notify_ai_busy` doesn't break a turn).

## Verification

- `PYTHONPATH=src python3 -m pytest tests -q`.
- Live (needs a restart): run a plugin-authoring turn that generates a plugin. Expect the
  animation text to cycle random words while thinking, switch to "Validating profile" /
  "Generating plugin scaffold" as calls start, and return to random words between calls.
  Confirm in `eisyai.log` that `POST /api/ai/busy` is called on each change (about one per 7 s
  idle, plus one per tool phase).

## Notes

- The default `_IDLE_INTERVAL` (7 s) and the status phrases are easy to change after seeing
  them live.
