# OPENAI RESPONSES API SUPPORT (Implemented, 2026-10-28)

> **The code described below is implemented and tested.** `src/unified/adapters/
> openai_responses_adapter.py` (`OpenAIResponsesAdapter`), wired through
> `src/unified/adapters/__init__.py`, `src/unified/provider_clients.py`, and
> `src/unified/provider_dispatch_adapter.py`'s `_normalize()`; the `unified` tool set's native
> `web_search` wiring in `src/unified/runtime.py`'s `UnifiedRuntime.__init__` (see "Web search
> tool" below); tests in `tests/unified/adapters/test_openai_responses_adapter.py`,
> `tests/unified/test_provider_clients.py`, `tests/unified/test_provider_dispatch_adapter.py`,
> and `tests/unified/test_runtime.py`. Full `tests/unified` suite passes (1162 tests). See
> `README.md`'s "Supported Providers" table, `src/unified/runtime_config.md`'s `provider` field,
> and `src/unified/runtime_config.openai-responses.example.json` for the user-facing side of
> this. **Not yet done:** the actual production flip for the `gpt-6.1-sol` profile (its
> deployment-specific `runtime_config.json`, outside both repos, still says `"provider":
> "openai"` as of this writing) and the live-API verification in "Open questions" below.

## Why this doc exists

Production hit a dead end getting a reasoning-tier OpenAI model (`gpt-6.1-sol`, configured
for the `unified` profile in eisy-ai's deployment-specific `runtime_config.json`, not a file
in this repo) to use function tools at all on `chat.completions`. Three successive live-400
discoveries, all against `OpenAIAdapter.generate()`
(`src/unified/adapters/openai_adapter.py`):

1. Configured `reasoning_effort` (e.g. `"low"`) + `tools` present → 400: *"Function tools
   with reasoning_effort are not supported for gpt-6.1-sol in /v1/chat/completions. To use
   function tools, use /v1/responses or set reasoning_effort to 'none'."*
2. Retried with `reasoning_effort: "none"` (the error's own suggested fix) → a **different**
   400: *"Unsupported value: 'reasoning_effort' does not support 'none' with this model.
   Supported values are: 'low', 'medium', 'high', and 'xhigh'."*
3. Retried a third time, omitting `reasoning_effort` entirely → **error 1 again**. This model
   reasons by default and can't be told not to; omitting the field just lets the server apply
   its own non-`"none"` default tier, which still conflicts with tools.

All three `chat.completions`-side parameters are exhausted. OpenAI's own error has pointed at
`/v1/responses` from the very first attempt. There is currently **zero** Responses API code
anywhere in this codebase (confirmed by a repo-wide grep for `responses.create`,
`/v1/responses`, `ResponsesAdapter` during the first investigation round) — the fix now
requires a genuinely new code path, not another parameter tweak. The retry-and-remember
scaffolding already built into `OpenAIAdapter` (`_reasoning_effort_tools_override`,
`_create_chat_completion`) stays as-is for every model that *does* work on `chat.completions`
— this doc only adds a second, explicitly-opted-into path for models that don't.

## Goals / non-goals

**Goal:** let a profile's `runtime_config.json` entry opt a specific OpenAI model into the
Responses API, so function tools work for models like `gpt-6.1-sol` that reject them on
`chat.completions` under every `reasoning_effort` setting.

**Non-goals for v1** (explicitly deferred, not because they're hard, but because nothing
today needs them):
- `previous_response_id`/`store`-based server-side conversation state. `AgenticLoop.run`
  (`src/unified/loop.py:144-201`) already resends the full accumulated `messages` list every
  round regardless of provider — the new adapter follows that same stateless-per-call
  contract, exactly like every existing adapter. Adopting server-side state would be a
  separate, `AgenticLoop`-level change affecting every provider, not an OpenAI-specific one.
- Any of the Responses API's other first-party tool types *besides* web search (code
  interpreter, computer use, MCP, image generation, shell). Web search is in scope — see
  "Web search tool" below — because it's the one native tool this codebase already has a
  working precedent for (Claude's `web_search_20250305`) and the one most likely to matter for
  a customer-facing `unified` conversation.
- `background: true` (async/polling responses). Nothing in this codebase's synchronous
  request/response or streaming model has a slot for it.
- Retrofitting every other OpenAI-family model onto Responses. Only the specific profile(s)
  that need it opt in; `chat.completions` stays the default for every model that already
  works there.

## Rejected alternative: automatic in-adapter fallback

The obvious-looking alternative — detect the exhausted cascade inside `OpenAIAdapter`'s
existing `_create_chat_completion` retry loop and silently re-issue the same turn against
`/v1/responses` — was rejected:

- **Mid-conversation shape mismatch.** `_create_chat_completion` only ever sees *this one
  call's* `kwargs["messages"]`. By the time a cascade exhausts on, say, iteration 3 of a
  multi-round tool-calling turn, `messages` already contains one or more
  `chat.completions`-shaped assistant/tool round-trips (from `OpenAIAdapter.
  build_tool_round_trip_messages`, native `{id, type, function:{name,arguments}}` tool_calls
  shape). Translating those into Responses' `function_call`/`function_call_output` input items
  *after the fact*, mid-turn, is a second translation layer bolted onto the first, purely to
  paper over a conversation that was never going to work on `chat.completions` from turn one.
- **Silent, surprising behavior change.** A fallback that transparently swaps APIs mid-turn
  changes streaming event shapes, usage field names, and tool-call id semantics underneath a
  caller that has no way to know it happened. This repo's own `design/developers/
  merged-toolsets.md` rejected a conceptually similar "automatic, un-opted-into" routing
  decision for the same reason (see its rejected alternative 4): explicit per-profile config
  beats implicit runtime magic every time this codebase has had the choice.
- **It's unnecessary.** `ProviderDispatchLLMAdapter` + `build_provider_clients_from_runtime_
  config` already give every profile its own adapter instance, selected by an explicit
  `provider` string in that profile's config (see below) — the exact mechanism needed already
  exists and just needs one more case added to it.

## The adopted design

### One new adapter class, selected by an explicit per-profile `provider` value

Add `OpenAIResponsesAdapter` in a new file, `src/unified/adapters/openai_responses_adapter.py`,
subclassing `OpenAIAdapter` (not `LLMAdapter` directly) specifically to inherit the parts that
are genuinely provider-shape-independent:
- `_normalize_openai_schema`/`_normalize_schema_node`/`_allow_null` — OpenAI strict-mode JSON
  Schema normalization is identical on both endpoints (same `strict` semantics, same
  `additionalProperties`/`required` rules); only the *wrapper* around the schema differs
  (nested `function.parameters` vs. flat `parameters` — see below).
- `_coerce_json` — parsing a stringified tool-argument blob back to a dict is endpoint-agnostic.
- `__init__` — the exact same `AsyncOpenAI(api_key=..., base_url=...)` client exposes
  `.responses.create()` alongside `.chat.completions.create()`; no second client needed.
- `_extra_headers` hook — preserved in case a future Responses-routed profile needs it (e.g. a
  Grok-style header), though nothing uses it yet.

Everything else is overridden with a fresh implementation, because the request/response shape
genuinely differs (confirmed directly against the installed SDK, `openai==3.3.0`,
`openai.resources.responses.responses.AsyncResponses.create`):

| | `chat.completions` (`OpenAIAdapter`) | `/v1/responses` (`OpenAIResponsesAdapter`) |
|---|---|---|
| System prompt | `{"role": "system", ...}` messages, merged | top-level `instructions: str` param |
| History/turn input | `messages: [{"role","content"}, ...]` | `input: [...]` (`EasyInputMessageParam`-shaped dicts for plain turns) |
| Reasoning control | flat `reasoning_effort: "low"` | nested `reasoning: {"effort": "low"}` |
| Tool definition | `{"type":"function","function":{"name","description","parameters","strict"}}` | flat `{"type":"function","name","description","parameters","strict"}` (`FunctionToolParam`) |
| Tool call (model→us) | `message.tool_calls[i]` = `{"id","type","function":{"name","arguments"}}` | an `output` item, `{"type":"function_call","call_id","name","arguments","id"}` (`ResponseFunctionToolCall`) |
| Tool result (us→model) | `{"role":"tool","tool_call_id","content"}` message | `{"type":"function_call_output","call_id","output"}` input item (`FunctionCallOutput`) |
| Non-streaming result | `ChatCompletion`, text at `.choices[0].message.content` | `Response`, text via `.output_text` / walking `.output` |
| Streaming | async iterator of `ChatCompletionChunk`s, `.choices[0].delta` | async iterator of typed `ResponseStreamEvent`s, discriminated by `.type` |
| Usage fields | `prompt_tokens`/`completion_tokens`/`prompt_tokens_details.cached_tokens` | `input_tokens`/`output_tokens`/`input_tokens_details.cached_tokens` (different names, same shape of information — logged as-is either way; `PromptLogManager.write_usage`, `src/utils/prompt_log.py:151-172`, just persists whatever dict it's handed, no schema assumed) |

### `generate()`: request translation

```python
def _split_messages_for_responses(self, messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Returns (instructions, input_items). A message already in Responses-
    native shape (has a "type" of "function_call"/"function_call_output" --
    i.e. it's something *this adapter's own* build_tool_round_trip_messages
    appended in an earlier round of the same turn) passes through
    unchanged. A plain {"role","content"} message with role == "system" is
    pulled out into `instructions` instead of `input` (Responses' dedicated
    system-prompt channel); everything else becomes an EasyInputMessageParam
    dict. Mirrors OpenAIAdapter._normalize_messages's content-must-be-a-
    plain-string coercion for non-str content.
    """
```

- System messages: `AgenticLoop.run` emits one message per prompt section (`loop.py:159-161`,
  "each becomes its own system message so an adapter can give each its own cache breakpoint").
  Responses API has no equivalent of Claude's manual cache-breakpoint array (its own prompt
  caching is automatic server-side), so all system-role sections are simply joined (blank-line
  separated, same join `OpenAIAdapter._normalize_messages` already uses for consecutive system
  messages) into the single `instructions` string. No cache-breakpoint parity to preserve here
  — there isn't one on the `chat.completions` side for OpenAI either (only Claude has it).
- `reasoning_effort`: when configured, forwarded as `reasoning={"effort": cfg["reasoning_effort"]}`.
  No retry cascade needed here — exhausting that cascade on `chat.completions` is the entire
  reason this model needs Responses in the first place. If a *future* model hits an analogous
  value-rejection on `/v1/responses`, the same discover-and-remember pattern
  (`OpenAIAdapter._create_chat_completion`'s shape) can be reapplied here then — not
  speculatively built now against an error that hasn't happened.
- `tools`: exported via a new flat-shape `export_tools()` override (see table above) —
  everything else about strict-mode schema normalization is inherited unchanged.

### `generate()`: non-streaming response handling

```python
response = await self._client.responses.create(
    model=model, instructions=instructions, input=input_items,
    tools=tools or NOT_GIVEN, reasoning=..., extra_headers=extra_headers,
)
text = response.output_text  # Response's own convenience property
function_calls = [item for item in response.output if item.type == "function_call"]
```
`parse_tool_calls()` override reads `response["output"]` for `type == "function_call"` items
(`call_id`/`name`/`arguments`), mirroring `OpenAIAdapter.parse_tool_calls`'s existing (currently
dead/untriggered) Responses-shaped branch at `openai_adapter.py:422-437` — that branch was
written speculatively for exactly this and has never had a real caller until now.

### `generate()`: streaming response handling

`await self._client.responses.create(stream=True, ...)` returns an
`AsyncStream[ResponseStreamEvent]`; each event is discriminated by `.type`:

- `"response.output_text.delta"` → `event.delta` is the incremental text chunk; forwarded to
  `stream_handler` and accumulated, same role as `OpenAIAdapter`'s `delta.content` handling.
- `"response.output_item.done"` → `event.item` is the *fully assembled* item (no manual
  fragment-accumulation needed the way `OpenAIAdapter`'s streaming path reassembles tool-call
  argument fragments across chunks by index — Responses hands back the complete item in one
  event). When `item.type == "function_call"`, capture it directly.
- `"response.completed"` → `event.response` is the final `Response`; its `.usage` becomes this
  call's `usage` dict via the existing `_usage_dict()` helper (inherited unchanged — it already
  just calls `.model_dump()` on whatever usage object it's handed).

### `build_tool_round_trip_messages()`

Returns Responses-native items directly (not a `{"role":"assistant","tool_calls":[...]}`
message the way `OpenAIAdapter` does) — an assistant-turn echo isn't required the way OpenAI's
`chat.completions` validates it; Responses just wants the prior `function_call` output item(s)
followed by their `function_call_output` input item(s):
```python
return [
    {"type": "function_call", "call_id": tc.call_id, "name": tc.name, "arguments": json.dumps(tc.args)},
    {"type": "function_call_output", "call_id": tc.call_id, "output": stringify_tool_result(result)},
    ...
]
```
These round-trip right back in on the next round via `_split_messages_for_responses`'s
pass-through branch (see above) — only possible because the loop always resends full history,
so there's no partial-state staleness risk, same reasoning `OpenAIAdapter`'s current
`build_tool_round_trip_messages` docstring already gives for its own equivalent.

### Config wiring (three small, mechanical additions)

1. `src/unified/adapters/__init__.py`: export `OpenAIResponsesAdapter`.
2. `src/unified/provider_clients.py`'s `build_provider_clients_from_runtime_config`: one more
   `elif provider in {"openai-responses", "openai_responses"}:` branch, identical shape to the
   existing `openai` branch but instantiating `OpenAIResponsesAdapter` and registering under
   `clients.setdefault("openai_responses", adapter)`.
3. `src/unified/provider_dispatch_adapter.py`'s `_normalize()`: add `"openai-responses" →
   "openai_responses"` to the alias table (consistent with every other hyphen/underscore
   alias already there).

No change needed to `runtime_config.py`'s `_coerce_runtime_profile`/`_normalize_provider_name`
— `provider` is already free-form there (an unrecognized string just passes through
lowercased unchanged; no profile-time validation gate blocks it), and `_PROVIDER_CAPABILITIES`
only feeds the currently-unconsumed `supports_system_role` flag (grepped: no reader anywhere
outside `runtime_config.py` itself) — the new adapter handles system-role mapping internally
regardless of that flag's value, so it's harmless to leave unset (defaults to `True`, same as
every other provider).

**The actual flip**, once this ships: the `gpt-6.1-sol` profile's deployment-specific
`runtime_config.json` (outside both repos, set via eisy-ai's admin UI per
`EisyaiControllerNode.py`) changes `"provider": "openai"` → `"provider": "openai-responses"`
for that one profile. Every other OpenAI profile is untouched. Rollback is the same one-line
edit in reverse.

## Web search tool

### Current state: `unified` has no web search today, for any provider

The `unified` tool set — the one `gpt-6.1-sol` actually runs under (production logs: `"unified:
turn failed"`) — has **zero web search capability today, regardless of provider**.
`UnifiedRuntime.__init__`'s default bundle for it is hardcoded
(`src/unified/runtime.py:261-266`):
```python
self._tool_sets["unified"] = ToolSetBundle(
    tool_spec_paths=None,
    dispatch_factory=lambda _ctx: DispatchBundle(dispatch=self._default_dispatch),
    system_prompt_builder=build_system_prompt_sections,
)
```
No `extra_tools`, ever. `AgenticLoop.__init__`'s own docstring (`loop.py:92-95`) says so
explicitly: `extra_tools` is "unset by every other caller, including the customer tool set,
which never touches either." Web search exists only for `plugin_authoring`, gated in
`_build_plugin_authoring_tool_set` (`run_unified_runtime.py:803-895`), two mutually exclusive
ways:
- A plain function tool (`plugin_authoring/tools/tool_search_web.json`) dispatched to Brave or
  Tavily (`plugin_authoring/handlers/discovery.py:142,160,176`) whenever `search_engine` +
  `search_engine_api_key` are configured — provider-agnostic, already works for any adapter.
- Claude's native server-side tool, `extra_tools=[{"type": "web_search_20250305", ...}]`
  (`:843-889`), injected only when the resolved provider is `"claude"` and no explicit
  `search_engine` override is set — bypasses `ToolSpec`/dispatch entirely; results harvested
  via `on_raw_response` → `_record_native_search_evidence` (`:776-800`), never becoming a
  `ToolCall`.

### Decision: OpenAI's native `web_search` tool, wired into `unified` for Responses profiles

The Responses API has its own direct analog to Claude's native tool — confirmed in the
installed SDK (`openai/types/responses/web_search_tool_param.py`, `WebSearchToolParam`): a flat
```json
{"type": "web_search", "filters": {"allowed_domains": [...]}, "user_location": {...}}
```
dict (both `filters`/`user_location` optional — `{"type": "web_search"}` alone is valid).
Results come back as an `output` item of type `"web_search_call"`
(`openai/types/responses/response_function_web_search.py`): `id`, `status`
(`in_progress`/`searching`/`completed`/`failed`), and `action` — a discriminated union of
`search` (`query`/`queries`, `sources`: list of `{"type":"url","url":...}`), `open_page`
(`url`), or `find_in_page` (`pattern`, `url`). This is never a `function_call` item, so it's
already naturally skipped by `parse_tool_calls()`'s `type == "function_call"` filter — no risk
of it being mistaken for a dispatchable tool call.

Mirroring Claude's existing pattern but wired for `unified` (since that's where this model
lives, not `plugin_authoring`): generalize `UnifiedRuntime.__init__`'s hardcoded `unified`
bundle (`runtime.py:261-266`) to resolve `unified`'s provider via the same helper
`run_unified_runtime.py` already uses for `plugin_authoring`'s equivalent decision
(`resolve_llm_profile(runtime_config, preferred_key="unified")`, `runtime.py:99-111`) and, when
it normalizes to `"openai_responses"`, add `extra_tools=[{"type": "web_search"}]` to that
`DispatchBundle`:
```python
unified_provider = resolve_llm_profile(runtime_config, preferred_key="unified").get("provider")
unified_extra_tools = (
    [{"type": "web_search"}]
    if ProviderDispatchLLMAdapter._normalize(unified_provider or "") == "openai_responses"
    else None
)
self._tool_sets["unified"] = ToolSetBundle(
    tool_spec_paths=None,
    dispatch_factory=lambda _ctx: DispatchBundle(
        dispatch=self._default_dispatch, extra_tools=unified_extra_tools
    ),
    system_prompt_builder=build_system_prompt_sections,
)
```
No dispatch handler needed for `web_search_call` — same as Claude's, it's informational only.
A small evidence-harvester (own helper, not shared with plugin_authoring's
`_record_native_search_evidence` — the item shape differs enough that forcing a shared
function would just mean branching inside it) passed as `on_raw_response` so the sources
OpenAI's search actually used are visible/loggable instead of silently discarded, matching the
intent of the existing Claude precedent even though the concrete shape differs.

### Explicitly out of scope for this pass

Extending the Brave/Tavily generic fallback tool into `unified` for providers *other than*
`openai_responses`/Claude. That gap is real (`unified` has no web search for Grok, Gemini,
llama.cpp, or plain-`chat.completions` OpenAI either) but it's independent of "migrate this one
model to `/v1/responses`" — a separate follow-up if wanted, not bundled into this change.

## Testing plan

New `tests/unified/adapters/test_openai_responses_adapter.py`, mirroring
`test_openai_adapter.py`'s established conventions (`adapter._client.responses.create =
fake_create` monkeypatching, `_fake_*_response()` `SimpleNamespace` builders):

- `export_tools()` produces the flat shape, strict-mode normalization still applies (reuse the
  existing strict-mode test fixtures/tool files under `src/unified/tools/`).
- Non-streaming `generate()`: text-only reply, a single function-call reply, a multi-tool-call
  reply — asserting `parse_tool_calls()`/`to_canonical_tools()` produce the same canonical
  `tool_use` shape every other adapter does (so `AgenticLoop` needs zero changes).
- Streaming `generate()`: text deltas forwarded to the handler in order; a `function_call`
  captured correctly from a `response.output_item.done` event; usage captured from
  `response.completed`.
- `build_tool_round_trip_messages()` round-trips correctly into a second `generate()` call
  (i.e. the pass-through branch in `_split_messages_for_responses` actually recognizes what
  this method just produced).
- `reasoning_effort` configured → forwarded as `reasoning={"effort": ...}`; not configured →
  `reasoning` omitted entirely (mirrors `test_reasoning_effort_omitted_when_not_configured`).
- System-role messages (single and multiple sections) joined into `instructions`, never
  appear in `input`.
- A live-shaped `BadRequestError` (unrelated to reasoning_effort) propagates unchanged — no
  adapter should ever swallow an error it doesn't specifically recognize.
- `UnifiedRuntime.__init__`'s `unified` bundle gets `extra_tools=[{"type": "web_search"}]` when
  `unified`'s resolved provider normalizes to `"openai_responses"`, and `None` for every other
  provider (regression check: existing non-Responses profiles must see no behavior change).
- A `"web_search_call"` output item is never surfaced as a `ToolCall`/dispatched, and the
  evidence-harvester extracts `action.sources` correctly from a fake `response.completed`
  event carrying one.

Also run the full `pytest tests/unified -q` suite afterward — this is purely additive (new
file, three mechanical wiring edits elsewhere); nothing about `OpenAIAdapter`'s own behavior
changes, so the existing 1142 tests should be unaffected.

## Open questions for whoever picks this up

Both of the items below need a real call against a live key/endpoint for this model — there's
no way to answer them from code reading or mocked tests, and that live access isn't available
yet. **Deferred, not a blocker for landing the code** — the implementation and its unit tests
can proceed entirely against mocked `responses.create()` calls; these just need checking
before (or immediately after) the production config is actually flipped for `gpt-6.1-sol`:

- Does `gpt-6.1-sol` (or whatever real model this profile ends up naming) actually support
  *parallel* tool calls on Responses the same way `chat.completions` does? `parallel_tool_calls`
  is a real Responses param; worth a real-API smoke test once a key is available.
- Does this model's `reasoning` + `tools` combination — which failed three ways on
  `chat.completions` — actually succeed on `/v1/responses`, as OpenAI's own error claimed?
  This is the entire premise of this doc; it's extremely likely true (it's OpenAI's own stated
  fix), but hasn't been, and can't yet be, confirmed against the real API.
- Does the native `web_search` tool actually work for this model on `/v1/responses` the same
  way it does for other reasoning models? Not every model supports every first-party tool;
  worth confirming alongside the above once live access exists.
