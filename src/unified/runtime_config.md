# Runtime Config Reference

Field-by-field reference for the JSON file passed to `run_unified_runtime.py` via
`--runtime-config` (see `runtime_config.example.json`/`.gemini.example.json`/`.grok.example.json`/
`.openai.example.json`/`.openai-responses.example.json` in this directory for copy-paste starting
points, one per provider). A real
CLI invocation always supplies a path -- argv only ever carries strings -- but an in-process
caller invoking `run_unified_runtime.main()` directly may instead pass an already-parsed dict of
this same shape, skipping the file entirely (see `_load_runtime_config`'s own docstring).
Everything below describes the shape of the JSON/dict itself; it applies identically either way.

For the *why* behind this schema -- why settings are split the way they are between top-level
and per-profile, and why three fields are deliberately kept **out** of this file entirely -- see
[`design/developers/merged-toolsets.md`](../../design/developers/merged-toolsets.md). This doc
only covers *what each field does and what happens if you omit it*.

Loading and validation happens in `runtime_config.py`'s `_load_runtime_config`/
`_coerce_runtime_profile`; this reference describes that code's actual behavior, not aspirational
behavior -- if something here looks surprising, it's surprising in the code too, not a doc bug.

## Shape

```json
{
  "max_tool_set_switches_per_turn": 1,
  "search_engine": "brave",
  "search_engine_api_key": "${SEARCH_ENGINE_API_KEY}",
  "nucore_runtime": {
    "unified": { "provider": "claude", "model": "...", "...": "..." },
    "plugin_authoring": { "provider": "claude", "model": "...", "...": "..." }
  }
}
```

`nucore_runtime` is required and must be a non-empty object. Every other top-level key is
optional. Each key inside `nucore_runtime` is a **profile** -- a named, independently-configured
tool set. The loader itself is generic over any number of profiles with any names, but only two
names currently have real tool-set wiring behind them: `unified` (the customer-facing tool set)
and `plugin_authoring` (the developer/plugin-authoring tool set). A profile with any other name
loads and validates fine but isn't reachable by anything today.

## Top-level fields

| Field | Type | Default | Effect |
|---|---|---|---|
| `nucore_runtime` | object | *(required)* | Maps profile name → profile config (see below). Must contain at least one profile, and at least one must resolve to `enabled: true` (see `enabled` below) or loading raises `ValueError`. |
| `max_tool_set_switches_per_turn` | integer | `1` | Caps how many times one user turn may chain from one tool set into the other after a model-driven `switch_to_developer_mode`/`switch_to_customer_mode` call. Exceeding it returns an ordinary `{"error": ...}` tool result, not a crash. |
| `search_engine` | `"brave"` \| `"tavily"` | *(none)* | Web-search provider for `plugin_authoring`'s `search_web` tool (and `unified`'s, since this is shared by either tool set's discovery tools). Omitting it does **not** disable web search outright for `plugin_authoring`: when that tool set's resolved provider is Claude, Claude's own native `web_search` server tool is used automatically instead, no second API key needed. For `unified` specifically, omitting it when `unified`'s own resolved provider is `openai-responses` likewise falls back to OpenAI's own native `web_search` tool (see `UnifiedRuntime.__init__`, `design/migration-to-openai-response-api.md`) -- for any *other* provider on either tool set, omitting this means no web search capability at all for that tool set. Setting it explicitly always forces the Brave/Tavily fallback, on any provider including Claude/`openai-responses`. |
| `search_engine_api_key` | string | *(none)* | API key for `search_engine`, in the same `"${ENV_VAR_NAME}"` placeholder form as a profile's `api_key` (see "Secrets" below) -- never a literal key in the file. Required for `search_engine` to actually take effect; `search_engine` with no key behaves the same as neither being set. |
| any other key | -- | -- | Silently ignored -- never an error. This is also how a documentation-only key survives in a file format with no comment syntax; see "Documentation-only keys" below. |

Three settings that look like they belong here -- `preferences_dir`, where the debug prompt log
goes, and `plugin_output_root` -- are **not** read from this file at all, by design: see "What's
deliberately not here" below.

## Per-profile fields (`nucore_runtime.<name>`)

| Field | Type | Default | Effect |
|---|---|---|---|
| `provider` | string | *(required)* | Which LLM backend this profile calls. Accepted values (case-insensitive, aliases shown): `claude`/`anthropic`, `openai`/`gpt`, `openai-responses` (same OpenAI account, routed to `/v1/responses` instead of `/v1/chat/completions` -- see "Provider-specific caveats" below), `gemini`/`google`, `grok`/`xai`/`x.ai`, `llamacpp`/`llama_cpp`/`llama.cpp`. Empty/missing raises `ValueError`. |
| `model` | string | *(none)* | Passed straight through to the provider's API as the model identifier (e.g. `"claude-sonnet-5"`, `"gpt-5.6-luna"`). Not validated at load time -- an unknown model string just fails at call time with whatever error the provider's API returns. |
| `api_key` | string | *(none)* | Either a literal key or a `"${ENV_VAR_NAME}"` placeholder (see "Secrets" below). When omitted (or the placeholder resolves empty), falls back to a provider-specific environment variable: `ANTHROPIC_API_KEY` (claude), `OPENAI_API_KEY` (openai and openai-responses -- same account, same key), `XAI_API_KEY`/`GROK_API_KEY` (grok), `GEMINI_API_KEY` (gemini). `llama.cpp` needs no real key -- it falls back to the literal string `"no-key"` if nothing else is set, since most llama.cpp servers run unauthenticated. |
| `url` | string | *(none)* | Custom base URL for the provider's API (e.g. a self-hosted llama.cpp server, or an OpenAI-compatible proxy). `null`/omitted uses the provider's own default endpoint. |
| `enabled` | boolean | `true` | Whether this tool set is reachable at all. `false` removes it from `enabled_profiles` without deleting the rest of its config -- the profile's settings stay in the file, just dormant. At least one profile across the whole file must end up enabled. |
| `max_iterations` | integer | `8` | Hard cap on tool-call rounds within one `AgenticLoop.run()` call for this tool set. Hitting it without a final answer returns a hardcoded "ran out of steps" message rather than looping forever. |
| `max_turns` | integer | `20` | How many past turns of this tool set's own conversation history are kept and replayed on each new call (older turns are dropped, oldest-first). Also used as this profile's `SessionStore` turn cap. |
| `max_tokens` | integer | *(provider-adapter default; typically `4096`)* | Requested max output tokens per LLM call. **Only forwarded to the API for `claude`, `gemini`, and `llamacpp` providers.** For `openai` and `grok`, this field is currently read but never sent -- see "Provider-specific caveats" below. |
| `temperature` | number | *(none; provider default applies)* | Sampling temperature. **Only forwarded for `claude` and `gemini`.** For `openai` and `grok`, silently not sent at all, regardless of what's configured here -- see "Provider-specific caveats" below. For `claude`, a model that has deprecated this parameter (confirmed as of this writing for `claude-sonnet-5` and `claude-haiku-5-5`, and likely the rest of the 5.5 family) gets a 400 on the first call per process and is automatically retried without it -- see `claude_adapter.py`'s `_no_temperature_models`. |
| `reasoning_effort` | string | *(none)* | Forwarded only to `openai`-family providers, and only when truthy (e.g. `"none"`, `"low"`). No effect for any other provider. For `openai` (`/v1/chat/completions`): some reasoning-tier models reject function tools unless this is explicitly set; `OpenAIAdapter` auto-discovers and remembers, per model, whether that model additionally needs it forced to `"none"` or omitted entirely once tools are involved -- see "Provider-specific caveats" below and `design/migration-to-openai-response-api.md`'s "Why this doc exists". For `openai-responses` (`/v1/responses`): forwarded as the nested `reasoning: {"effort": ...}` shape that endpoint actually expects (not the flat field); no auto-discovery cascade here, since a model needing that endpoint at all is (by construction) one whose `chat.completions` cascade already exhausted every value. |
| `cache_ttl` | `"5m"` \| `"1h"` | *(none -- Anthropic's default 5-minute ephemeral cache)* | Prompt-cache breakpoint TTL. **Claude-only** -- silently has no effect for any other provider. `"1h"` costs 2x to write vs. 1.25x for `"5m"`, so it only pays off when turns in the same conversation are typically 5-60 minutes apart. Any other value raises `ValueError`. |
| `stream` | boolean | `false` | Whether this profile's responses stream token-by-token. Streaming only actually happens when the caller also supplied a live `StreamHandler` (always true when running via `run_unified_runtime.py`, in both REPL and WebSocket modes) -- with `stream: true` but no handler, this profile behaves as non-streaming regardless. |
| `history_token_budget` | integer | `20000` | Soft token budget this tool set's conversation history is compacted down toward (see `history_compaction.py`) before being replayed into a new call -- independent of `max_turns`, which caps by *turn count* rather than token count. |
| `fabrication_guard_mode` | `"off"` \| `"log"` \| `"block"` | `"log"` | How the agentic loop reacts when the model claims an action completed without having called a tool this turn. `"off"`: no check. `"log"`: detect and record a flag line in the prompt log, but never change what the customer sees. `"block"`: also inject a corrective nudge and force one more round (up to `max_fabrication_retries` times) instead of returning the flagged reply. Any other value raises `ValueError`. |
| `max_fabrication_retries` | integer | `1` | How many corrective retries `fabrication_guard_mode: "block"` gets before giving up and returning a hardcoded non-claim fallback instead of the model's (possibly false) claim. No effect in `"off"`/`"log"` mode. |
| `supports_system_role` | boolean | provider's own capability table (`true` for every provider currently listed) | Computed and stored on the normalized profile, but **not currently read by any adapter** -- every adapter already handles a `system`-role message its own way regardless of this flag. Setting it has no observable effect today; it's reserved from the original per-provider capability design. |
| any other key | -- | -- | Silently ignored, same as at the top level -- see "Documentation-only keys" below. |

## Secrets: the `${ENV_VAR_NAME}` convention

`api_key` (per-profile) and `search_engine_api_key` (top-level) both accept a value of the exact
form `"${SOME_ENV_VAR}"`. When a value matches that shape, the literal env var *name* between the
braces is looked up at load time (in `--secrets-file`'s contents if one was given, else
`os.environ`) and substituted; the resolved value is never written back to the file. Any value
that doesn't match that exact `${...}` shape is used as a literal string unchanged (so a literal
inline key also works, it's just not recommended for anything checked into version control).

## Provider-specific caveats

These aren't bugs in this config file's schema -- they're real differences in how each adapter
(`src/unified/adapters/`) builds its request, worth knowing before assuming a field "isn't
working":

- **`temperature`/`max_tokens` are silently dropped for `openai`, `openai-responses`, and
  `grok`.** `OpenAIAdapter` (which `GrokAdapter` inherits from unchanged) only forwards either
  field when its `_forward_temperature_and_max_tokens` class flag is `True` -- `False` by
  default, because OpenAI's reasoning-tier models (e.g. `gpt-5-mini`) reject both outright.
  `OpenAIResponsesAdapter` (`openai-responses`) doesn't forward them either -- its `generate()`
  never reads either config key at all, same net effect via a different mechanism (no flag to
  flip; `/v1/responses` has its own differently-named `max_output_tokens` param this adapter
  doesn't currently map `max_tokens` onto). `LlamaCppAdapter` overrides the flag back to `True`.
  `claude` and `gemini` always forward both.
- **`cache_ttl` only affects `claude`.** No other adapter reads it.
- **`reasoning_effort` only affects `openai`-family providers** (`openai`, `openai-responses`,
  and anything else built on `OpenAICompatibleAdapter`), and only when non-empty. Shape differs
  by endpoint -- see the field's own row above.
- **`openai`'s `reasoning_effort`-vs-tools auto-discovery is per-model, not per-value.**
  `OpenAIAdapter._create_chat_completion` discovers, from a live 400, whether a given model
  needs the configured value downgraded to `"none"` when tools are present, and whether `"none"`
  itself is unsupported (some models accept `reasoning_effort` but never accept disabling it) --
  in which case it omits the field entirely instead. If a model exhausts *all three* options
  (configured value, `"none"`, omitted) and tools are still rejected, it needs the
  `openai-responses` provider instead -- see `design/migration-to-openai-response-api.md`, which
  documents exactly this case for one such model (`gpt-6.1-sol`).
- **`claude-sonnet-5` and `claude-haiku-5-5` (as of this writing) reject `temperature`
  outright** -- a 400 the adapter catches and retries without the parameter, once per process
  per model. Likely true of the rest of the Claude 5.5 family too, though only these two are
  directly confirmed. See the `unified`/`plugin_authoring` profiles' own `_notes` fields in
  `runtime_config.example.json` for exactly this case, documented in place.

## Documentation-only keys

JSON has no comment syntax, and the loader never validates "no unrecognized keys may be
present" -- at either the top level or inside a profile, an unrecognized key is read and ignored,
never an error. The convention used across the example files in this directory is `_notes`: a
plain string (or array of strings) carrying a fact worth recording for a future reader, with
nothing behind it. For example:

```json
"unified": {
  "_notes": "No 'temperature' here on purpose -- claude-haiku-5-5 rejects it; see claude_adapter.py's auto-retry for what would happen if it were set.",
  "provider": "claude",
  "model": "claude-haiku-5-5"
}
```

## What's deliberately not here

`preferences_dir` (where customer preferences/aliases are stored), the debug prompt log's file
path/on-off switch, and `plugin_output_root` (the allowed root directory for generated plugin
scaffolds) are **CLI-only** -- `--preferences-dir`, `--prompt-log-file`, and
`--plugin-output-root` respectively -- and are never read from this file even if a key with that
name is present. The reasoning: this file holds settings a customer could reasonably supply
themselves (model, temperature, which tool sets are enabled); where preferences or debug logs
land on disk, and which root directory generated plugin scaffolds are confined to, are
deployment/host concerns that whoever launches the process controls, not whoever supplied the
config file. `--plugin-output-root` is required (`run_unified_runtime.py`'s `main()` raises
`ValueError` otherwise) whenever `nucore_runtime.plugin_authoring.enabled` is `true` -- the same
validation spirit this file used to apply to the now-removed `plugin_output_root` config key.
See [`design/developers/merged-toolsets.md`](../../design/developers/merged-toolsets.md)'s
"CLI-only, deliberately" section, and the top-level `README.md`'s CLI flag table, for all three
flags' exact behavior.
