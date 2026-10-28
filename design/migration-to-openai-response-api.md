# OPENAI RESPONSES API SUPPORT (PROPOSAL, 2026-10-28)

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
- Any of the Responses API's other first-party tool types (web search, code interpreter,
  computer use, MCP, image generation, shell). Only `function` tools are in scope — the only
  kind `export_tools()` has ever produced.
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

Also run the full `pytest tests/unified -q` suite afterward — this is purely additive (new
file, three mechanical wiring edits elsewhere); nothing about `OpenAIAdapter`'s own behavior
changes, so the existing 1142 tests should be unaffected.

## Open questions for whoever picks this up

- Does `gpt-6.1-sol` (or whatever real model this profile ends up naming) actually support
  *parallel* tool calls on Responses the same way `chat.completions` does? `parallel_tool_calls`
  is a real Responses param; worth a real-API smoke test once a key is available, not just
  unit tests against mocked responses.
- Confirm empirically (one real call against a live key, not guessed) that this model's
  `reasoning` + `tools` combination that failed three ways on `chat.completions` actually
  succeeds on `/v1/responses` as OpenAI's error claimed, before flipping the production config.
