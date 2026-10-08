# MERGED TOOL SETS: dynamic customer/plugin_authoring switching over one connection (PROPOSAL, 2026-10-07)

## Purpose

Today, `run_unified_runtime.py`'s `--tool-set` flag (`customer` default, or `plugin_authoring`) is
resolved exactly once per process, in `main()`, before the websocket server even starts, and
baked identically into every connection for that process's entire life (`_resolve_tool_set`,
`run_unified_runtime.py:698-797`). Getting access to both tool sets today requires two separate
server processes on two separate ports, and a client needs two separate websocket connections.

Goal: let both tool sets be reachable over one already-open websocket connection, switching
dynamically per turn — not a process restart — while preserving live token streaming, adding no
extra LLM call for routing, and not requiring a CLI-flag explosion to configure both sides.

## Rejected alternatives, and why

Kept for the same reason `design/design.md` keeps its own superseded architecture: so this
doesn't get re-proposed and re-litigated from scratch later.

1. **One process, one shared `runtime_config`/model for both tool sets.** Rejected: `customer`
   and `plugin_authoring` already use deliberately different models and `max_iterations`
   (`runtime_config.example.json`'s `max_iterations: 20` / `claude-haiku-4-5-20251001` vs.
   `runtime_config_plugin_authoring.example.json`'s `max_iterations: 50` / `claude-sonnet-5`) --
   a single shared config can't express that.

2. **A thin router in front of two fully isolated executables, fanning out to both and
   arbitrating on a can/cannot-handle reply.** Rejected on multiple independent grounds:
   - Cost/latency doubles every turn if "handle" means fully running the agentic loop before
     replying -- pays for and waits on both backends even for turns that never needed the
     second one.
   - Breaks live token streaming entirely (`StreamHandler`, the cache_control TTL tuning in
     `claude_adapter.py`) -- can't stream before the winner is known.
   - Can/cannot-handle is an uncalibrated self-assessment problem -- tool names literally
     overlap between the two sets, so both sides may plausibly claim "yes."
   - `UnifiedRuntime.handle_query` unconditionally commits to `SessionStore` before returning
     (`runtime.py:198`) -- a "losing" backend's unmodified code would still silently write a
     never-shown answer into its own permanent history.
   - Two independent `NuCoreInterface`/`IoXWrapper` instances means two independent PLM
     diagnostics fail-fast guards (`INSTEONDiagnostics._is_running`/`_plm_op_state`,
     `src/iox/diagnostics/insteon_diag.py`) that can't see each other -- a real hardware-safety
     regression versus today's single shared instance.

3. **Same two-executable-plus-router shape, but a single real LLM classification call picks
   one destination instead of fanning out.** Better cost/streaming shape, but: this repo already
   built almost exactly this once. `design/design.md` (marked "Superseded") describes nucore-ai's
   *original* router -> intent-handler -> synthesis architecture ("3+ LLM calls for anything
   requiring action"), retired specifically to collapse that into today's single unified agentic
   loop. That doc explicitly warns that a classification-only step, if reintroduced, "just
   reconstructs today's router under a new name." Reintroducing a router here means deliberately
   re-accepting a cost this project already paid down once, and risks the same scope creep that
   made the original router hard to maintain (`route_plan`/`route_context` threading, a dedicated
   continuation-classifier call, cross-intent memory hydration). Also doesn't cleanly solve a
   request that genuinely spans both tool sets in one turn.

4. **One process, two fully independent per-tool-set configs (own `runtime_config`/model/
   secrets/`max_iterations`), switching via an explicit sticky command, no LLM involved in the
   decision.** Architecturally sound -- zero added LLM calls, streaming untouched, full
   model/config isolation, diagnostics guard problem disappears (one process, one
   `NuCoreInterface`) -- but abandoned over **CLI surface area**: giving each tool set its own
   complete, separately-flagged configuration (`--runtime-config`, `--secrets-file`,
   `--search-engine`, `--plugin-output-root`, etc., each doubled) was judged impractical.

5. **Two isolated OS processes sharing a "gist."** Considered and rejected independently of the
   router question: `nucore_interface` wraps the single physical hub/PLM connection, and its
   diagnostics guard only protects against concurrent access *within one process* -- two
   processes would each hold an independent handle to the same hub with no way to see the
   other's in-flight operation. `SessionStore` is in-memory, per-process; sharing state across
   two executables means inventing a new cross-process mechanism for something that already
   trivially coexists in one process today. No precedent anywhere in this codebase for
   cross-process coordination.

The resolution that unlocked the final design: proposal 4's CLI-surface objection is specific to
exposing every setting as its own *flag*. It goes away entirely if both tool sets' settings live
in one *config file* instead -- which also turns out to let the per-tool-set model/config
differences from rejection 1 live somewhere sensible, with zero new CLI flags.

## The adopted design

### One process, one config file, two named profiles

Both tool sets' settings live in a single runtime config JSON, as two profiles under
`nucore_runtime`: `unified` (customer) and `plugin_authoring`. This reuses `resolve_llm_profile`
(`runtime.py:27-39`) exactly as it exists today -- switching tool sets becomes "resolve with
`preferred_key="plugin_authoring"` instead of `"unified"`," nothing new to build there.

```json
{
  "max_tool_set_switches_per_turn": 1,
  "search_engine": "brave",
  "search_engine_api_key": "${SEARCH_ENGINE_API_KEY}",
  "nucore_runtime": {
    "unified": {
      "enabled": true,
      "provider": "claude", "model": "claude-haiku-4-5-20251001", "api_key": "${ANTHROPIC_API_KEY}",
      "stream": true, "url": null, "temperature": 0.2, "cache_ttl": "5m",
      "max_iterations": 20, "max_turns": 20, "max_tokens": 64000, "history_token_budget": 20000,
      "fabrication_guard_mode": "log", "max_fabrication_retries": 1
    },
    "plugin_authoring": {
      "enabled": true,
      "plugin_output_root": "/path/to/allowed/root",
      "provider": "claude", "model": "claude-sonnet-5", "api_key": "${ANTHROPIC_API_KEY}",
      "stream": true, "url": null, "temperature": 0.2, "cache_ttl": "5m",
      "max_iterations": 50, "max_turns": 20, "max_tokens": 64000, "history_token_budget": 20000,
      "fabrication_guard_mode": "log", "max_fabrication_retries": 1
    }
  }
}
```

Global (top-level) keys apply regardless of active tool set: the switch cap and
`search_engine`/`search_engine_api_key` -- now usable by *either* tool set's discovery tools,
not just `plugin_authoring`'s, which it wasn't before.

**Preferences and the prompt log are deliberately *not* here, even though they're also
installation-wide, not tool-set-specific** -- see "CLI-only, deliberately" below for why they're
CLI-only instead.

Per-profile keys that used to be global and move here because they genuinely differ by tool set:
`max_iterations`, `max_turns`, `max_tokens`, `history_token_budget`, `fabrication_guard_mode`,
`max_fabrication_retries` -- the last two by the same logic as the first (no established reason
customer and plugin_authoring need the same fabrication-guard strictness any more than they need
the same `max_iterations`).

`plugin_output_root` moves from a required CLI flag (`--plugin-output-root`) into the
`plugin_authoring` profile itself, since it's specific to that side.

`search_engine_api_key` follows the *exact same* convention `api_key` already uses: a
`${VAR}`-style placeholder, resolved by generalizing the existing substitution check in
`provider_clients.py:66-70` (currently hand-rolled for the `api_key` field only) to also cover
this field. The literal secret value never appears in the file -- only the environment variable
*name* does, same as today.

### CLI-only, deliberately: `--preferences-dir` and `--prompt-log-file`

`preferences_dir` and the prompt log's location briefly lived in this file (both are
installation-wide settings, the same shape as `search_engine` above) before moving back out to
CLI-only flags -- `--preferences-dir` and `--prompt-log-file` (the latter replacing the former
`prompt_log_dir`/`prompt_log_enabled` pair: one flag is both the path and the sole on/off
switch, its mere presence enabling logging to exactly that file, creating missing parent
directories as needed). Neither is ever read from runtime config again, even if present there.

The distinction driving this: this config file holds *runtime* parameters a customer could
reasonably supply themselves -- which model, what temperature, which tool sets are enabled.
Where preferences or debug logs land on disk is a different kind of setting entirely -- a
deployment/host concern that whoever *launches* the process controls, not whoever *supplied*
the config file. A customer-editable config should not be able to redirect either of these to
an arbitrary path.

Since JSON has no comment syntax, a fact worth recording *in* the config file for a future
reader (e.g. "this model rejects `temperature`, see claude_adapter.py's auto-retry") has nowhere
obvious to go. The loader's answer: any key it doesn't recognize, at either the top level or
inside a profile, is silently ignored rather than rejected -- so a plain documentation-only key
(this doc uses `_notes` as the convention, see `runtime_config.example.json`) survives loading
untouched. Not a new mechanism -- `_coerce_runtime_profile` already only ever reads specific
keys via `.get(...)` and never validated "no other keys may be present"; this just states that
as deliberate, not incidental.

### `enabled` flag replaces "is the profile present" as the availability gate

Each profile gets an `enabled` field (default `true`). Whether `plugin_authoring` is reachable
on a given deployment is decided by `nucore_runtime.plugin_authoring.enabled`, not by whether the
CLI was started with a particular `--tool-set` value -- **`--tool-set` and `--plugin-output-root`
are removed as CLI flags entirely**. If `enabled` is `true` but `plugin_output_root` is missing,
loading fails (same validation spirit as today's `ValueError("--tool-set plugin_authoring
requires --plugin-output-root")`, just relocated to this check). The only cross-profile
requirement is that **at least one** profile is enabled -- see below, `unified.enabled: false` is
itself legal.

Flipping `enabled` to `false` disables that tool set without deleting its whole config block.
Because `_run_websocket_server`'s `handler` already reloads `runtime_config` fresh from disk for
every *new* connection (`run_unified_runtime.py:886-890` -- existing behavior, originally for
binding each connection's own `StreamHandler`), this takes effect for new connections without a
process restart. It does not retroactively affect connections already open at the moment of the
edit -- accepted as-is, no hot-reload-for-live-connections mechanism planned.

`unified.enabled: false` is a legal configuration, not rejected by validation -- the only
requirement is that **at least one** profile is enabled, not specifically `unified`. A new
connection's default active tool set is whichever single profile is enabled when only one is;
`unified` when both are (preserving today's behavior).

There is no eligibility gate beyond this deployment-level `enabled` flag -- any connected user
may invoke `/developer`/`/customer` or trigger the model-driven switch tool for any profile the
deployment has enabled. No separate per-user authorization layer.

### Dynamic switching: explicit command and model-driven tool, both present

No routing LLM call, no inference from UI context (that path was explored and abandoned --
`EisyUIContext`'s `context` payload is opaque to nucore-ai today, and the frontend that would
populate a "which page am I on" signal isn't available in any repo checked out alongside this
one). Two mechanisms coexist, as defense in depth:

- **Explicit, user-triggered**: typing `/developer` or `/customer` as a chat message. Sticky --
  once issued, every subsequent message on that connection routes to that tool set until the
  opposite command is issued. Parsed in `EisyUIContext.process_message`
  (`run_unified_runtime.py:61-99`), which already branches on message `"type"`; it gains a check
  for these two literal commands, setting a new sticky `active_tool_set` field on that
  connection's `EisyUIContext` (already one instance per connection, never shared --
  `run_unified_runtime.py:45-53`) and returning `None` (no query dispatched this turn), the same
  way a `"context"` message already returns `None` today. This check has to run in *two* places
  inside `process_message`, not one: the WebSocket `"type": "message"` branch, and the plain-
  string fallback its own `except` clause falls through to -- REPL/`--query` input is never
  JSON-wrapped (unlike a WebSocket payload), so without the second check these commands silently
  only worked over WebSocket, a real regression caught in testing, not a deliberate scoping
  decision.
- **Model-driven**: each tool set's own catalog gains one new tool -- `customer` gets
  `switch_to_developer_mode()`, `plugin_authoring` gets `switch_to_customer_mode()`. Each tool's
  description briefly summarizes what the *other* side can do (not its full prompt/tool catalog
  -- a short blurb, not a merge). The model calls it as a normal tool call within its existing
  agentic turn when it judges the request is out of scope for its current tool set -- no separate
  classification call, reusing the exact same single `AgenticLoop` round already in progress.

Both exist because the model usually catches the need to switch via the tool, but the user can
always force it manually if it doesn't.

### Mid-round chaining, not next-turn-only

When the switch tool is called mid-turn, the *same* turn continues under the new tool set rather
than stopping to ask the user to restate their request. Concrete motivating case: "check my
plugin and fix the profile if something's wrong" -- the model starts with tools available in
customer mode today (`get_plugin_capabilities`, `call_plugin` -- both already shared, identical
handlers either side), then discovers mid-turn that it needs `validate_profile` and
`regenerate_plugin_boilerplate`, both `plugin_authoring`-only. Next-turn-only handoff would force
a re-prompt and risk losing what was just found, unless conversation history is somehow shared
across the switch; mid-round handoff just continues.

Mechanically: `AgenticLoop` (`loop.py`) stays exactly as it is -- a generic, single-tool-set
loop with a fixed `tool_specs`/`dispatch` for the duration of one `run()` call, with no notion of
"tool set." The chaining logic lives one layer up, in the orchestration code wrapping
`UnifiedRuntime.handle_query`: when a tool result carries a "switch requested" signal, that layer
builds a *fresh* `AgenticLoop` from the other profile's resolved tools/dispatch/prompt and
continues -- two `AgenticLoop.run()` calls chained invisibly within one user-visible turn.

This recovers the one genuine advantage full merging had (handling a request that spans both
tool sets within a single turn) without merging's cost: a real, measured ~16.5K tokens / ~39%
prompt-size increase *on every single turn* (methodology: an empirically-derived 3.43
chars/token ratio, cross-referencing real per-call token usage in
`~/workspace/eisy-ai/logs/nucore.prompt.jsonl` against the exact system-prompt text logged for
the same calls, applied to `plugin_authoring`'s own system prompt (23,005 chars) and its 21
tool specs (33,575 chars, none overlapping by name with `customer`'s 34)). Under this design,
the cost of the other side is only paid at the moment a switch actually happens.

#### Handoff summary: the new tool set has its own history and never sees the old one's

Found via real-world testing, not design review: a customer asked for a SimpliSafe plugin, the
model searched the store, found nothing, and offered a numbered list of options including
"develop a custom plugin." The customer replied `"4"`. The switch fired correctly, but
`plugin_authoring`'s brand-new loop received *only* the literal trigger message -- `"4"`, with
none of the preceding exchange -- because that exchange lives in `unified`'s own
`ConversationHistory`, which by design (see "Session history" below) never crosses the switch.
The model, now in `plugin_authoring` with no idea what "4" refers to, replied `"4."`

The fix: `switch_to_developer_mode`/`switch_to_customer_mode` both take a **required**
`handoff_summary` string argument -- the calling model's own 1-3 sentence summary of what the
customer wants and why it's switching (e.g. `"Customer wants a custom plugin for their
SimpliSafe alarm; no store plugin exists for it. They confirmed by selecting the 'develop a
custom plugin' option I offered."`). This is the one point in the system that still has the full
context before the switch discards it -- reconstructing intent after the fact from conversation
history would mean violating the very isolation this design relies on to begin with.

Mechanically: the switch handler (`tool_set_switch.py`) echoes `handoff_summary` back onto the
sentinel dict it returns; `AgenticLoop` doesn't interpret it, just carries it on
`ToolSetSwitchRequested.handoff_summary` when it unwinds (same generic, tool-set-agnostic
treatment as the rest of the switch signal -- see "Mechanically" above). `UnifiedRuntime
.handle_query` rebuilds the next loop's `user_message` from the *original* query plus every
handoff note accumulated so far in this turn (plural, in case `max_tool_set_switches_per_turn`
is configured above 1 and a turn chains through more than one switch), via a small
`_build_user_message` helper -- rebuilt from scratch each time rather than nested, so a second
switch's wrapper doesn't wrap the first one's. Crucially, this wrapped message is only ever fed
to the LLM call itself: `history.append(query, final_text)` at the end of `handle_query` still
persists the clean, original `query` -- a later re-read of this tool set's history sees `"4"`,
not the synthetic handoff wrapper, which exists purely to inform this one call.

A missing or empty `handoff_summary` (the schema marks it required, but a model can still send an
empty string) does not block the switch -- it just means no extra context gets carried over,
the original pre-fix behavior, not a new failure mode.

The explicit `/developer`/`/customer` chat command needs none of this: it never resends a stale
trigger message at all, it just flips the connection's sticky `active_tool_set` for whichever
message the user sends *next* (see `EisyUIContext.process_message`) -- there is no message to
lose context from in the first place.

### Switch-count cap

`max_tool_set_switches_per_turn` (top-level config key, default `1`) bounds the worst case. A
counter local to one `handle_query` call, incremented each time the orchestration layer chains a
new loop after a switch-tool call; reset to zero at the start of every turn. If exceeded, stop
and tell the user to split their request into separate turns rather than looping indefinitely.
With the default of 1, a single turn's absolute worst case is bounded at roughly the larger of
the two profiles' `max_iterations` values added together (e.g. 20 + 50 = 70 rounds), as a hard
ceiling, not a typical case. The switch tool call itself needs no special accounting -- it's a
normal tool call, already counted as one round against whichever loop called it.

### How switch failures are surfaced: ordinary tool-result errors, not special-cased

Both failure modes -- the cap exceeded, and a request to switch to a disabled profile -- are
handled identically, and identically to how every other tool handler in this codebase already
reports failure (`dispatch.py`'s `execute_tool` returning `{"error": f"unknown tool '{name}'"}`;
`preferences.py`'s `_NOT_CONFIGURED`): the switch handler returns an `{"error": ...}`-shaped
result instead of performing the switch, and the model relays that to the user in its own words.
No new error-handling mechanism, no hardcoded system-generated string, no silent no-op. This
applies the same way whether the switch was requested via the model's own tool call or via the
user's explicit `/developer`/`/customer` command -- the explicit command is treated as an
immediate request to perform the same switch operation (succeed, or get back the same kind of
error), not a separate code path with its own messaging.

Example verbiage (illustrative, not binding -- the model phrases the actual reply):
- Cap exceeded: `{"error": "Already switched tool sets the maximum number of times allowed for
  one request (1). Finish with the current tool set, or ask the user to split this into separate
  requests."}`
- Disabled target: `{"error": "Developer tools are not enabled on this installation."}` (and the
  customer-mode-disabled symmetric case, on the rare deployment that runs plugin_authoring-only).

### Session history

`SessionStore` (`session_store.py`) is keyed by an arbitrary string, with no notion of tool set.
A composite key per active tool set (e.g. `f"{user_id}::{active_tool_set}"`) keeps the two
conversations from interleaving plain-text turns in one `ConversationHistory` when a connection
switches back and forth.

### Shared state is unaffected, which is why this is safe

`nucore_interface` (`IoXWrapper`) remains the single process-wide instance it already is today
(`run_unified_runtime.py:1043-1050`, shared across every connection -- confirmed in
`_run_websocket_server`'s own docstring). Every `UnifiedRuntime` built for either profile, on any
connection, receives the *same* object. Mid-round switching, or any number of switches in a row,
never creates a second hub connection or a second diagnostics guard -- the PLM fail-fast guard,
the preferences store (`nucore_interface._preference_store`), device/routine state, all stay
correctly coordinated regardless of which profile's dispatch table is currently calling in. This
is the direct, structural reason this design avoids the hardware-coordination risk that killed
the two-executable proposals.

### Tool-name collision safety

Confirmed today: the 5 overlapping tool names (`list_installed_plugins`, `plugin_ops`,
`get_plugin_capabilities`, `call_plugin`, `run_shell_command`) resolve to the literal same Python
handler function on both sides (`..handlers.plugin_management`/`..handlers.shell`) -- safe by
construction, not by convention. Any *new* name collision introduced later should raise loudly
rather than silently let one side shadow the other.

## Open questions -- resolved

1. **Eligibility beyond deployment-level `enabled`.** Resolved: no additional gate. Any connected
   user may use any profile the deployment has enabled.
2. **Cap-exceeded UX.** Resolved: surfaced as an ordinary `{"error": ...}` tool result, same
   convention every other handler already uses -- see "How switch failures are surfaced" above
   for example verbiage.
3. **Already-open connections** don't pick up an `enabled`/config edit until they reconnect.
   Confirmed as-is; no hot-reload-for-live-connections mechanism planned.
4. **`unified.enabled: false`.** Resolved: legal, not rejected by validation -- only "at least one
   profile enabled" is required. A disabled-profile switch attempt is handled the same way as the
   cap-exceeded case (an `{"error": ...}` result the model relays), not a validation-time failure.

## Status

Implemented. `runtime_config.py`'s two-profile schema, `provider_clients.resolve_env_placeholder`,
`switch_to_developer_mode`/`switch_to_customer_mode`, `EisyUIContext.active_tool_set`, the
mid-round chaining layer in `UnifiedRuntime.handle_query`, composite `SessionStore` keys, and the
consolidated per-provider example config files are all in place, with the `--tool-set`/
`--plugin-output-root` CLI flags removed in favor of config-driven `enabled_profiles`. Every
CLI flag that duplicated a setting the config file could already express
(`--stream`/`--no-stream`, `--max-iterations`, `--search-engine`, plus the already-dead
`--prompt_type` and the always-`true` `--json-output`) has also been removed, on the "config
file is the only source of truth for runtime parameters" principle this design is built on.
`--preferences-dir` and `--prompt-log-dir`/`--no-prompt-log` briefly followed the same path into
the config file, then moved back *out* to CLI-only (the latter collapsed into one
`--prompt-log-file` flag) -- see "CLI-only, deliberately" above for why these two are the
exception.

Three real-world-testing fixes landed after the initial implementation, all covered above:
the `handoff_summary` argument on both switch tools ("Handoff summary" above), making
`/developer`/`/customer` recognized from plain-string (REPL/`--query`) input, not just
WebSocket JSON messages (end of "Dynamic switching" above), and moving `--preferences-dir`/
`--prompt-log-file` back out of runtime config ("CLI-only, deliberately" above). A fourth fix,
unrelated to this design but found during the same testing round: a provider error (e.g. an
Anthropic 400) used to crash the entire REPL/WebSocket-connection process with no visibility
into the actual error body -- `_run_loop`/the WebSocket handler's per-message loop now catch
it, log the provider's own error body, and let the session continue.
