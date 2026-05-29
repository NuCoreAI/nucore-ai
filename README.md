# NuCoreAI Platform

## Goal

Convert natural language user queries into commands, queries, and programs for any NuCore-enabled platform (currently eisy).

## Quick Start

```shell
git clone https://github.com/NuCoreAI/nucore-ai.git
cd nucore-ai
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Running the Intent Runtime

The primary entry point is the intent handler runtime. It routes user queries to the correct intent handler and executes them against a NuCore backend.

### Handler Execution Contract

Intent handlers now use a runtime-managed execution flow:

1. Runtime builds messages via `handler.build_messages(...)`.
2. Runtime calls the LLM via `handler.call_llm(...)`.
3. Runtime extracts tool calls from the raw response.
4. Runtime calls `handler.handle(...)` for post-processing.

Current `handle` signature:

```python
async def handle(
  self,
  query,
  *,
  route_result=None,
  framework_context=None,
  raw_response=None,
  tool_calls=None,
):
  ...
```

This keeps handlers focused on tool execution and output shaping, while runtime
owns prompt/message/LLM orchestration.

### Custom Prompt for agent_response Handling

When runtime performs the tool-result follow-up pass (converting
`agent_response` into user-facing output), a handler can provide a dedicated
prompt template instead of reusing its main `prompt.md` content.

Override this hook in your `BaseIntentHandler` subclass:

```python
async def get_tool_result_prompt(self) -> str | None:
  return """
<<nucore_definitions>>
<<nucore_common_rules>>

---
# DEVICE STRUCTURE
<<runtime_device_structure>>
"""
```

Return `None` to keep the default flow (no dedicated tool-result system prompt
from the handler).

Create a runtime profile JSON first (see `src/intent_handler/runtime_assets/nucore_runtime.example.json`).

### Minimal (no backend)

```shell
python -m intent_handler.run_intent_runtime \
  --intent-dir src/intent_handler_directory \
  --runtime-config src/intent_handler/runtime_assets/nucore_runtime.example.json \
  --query "Turn on the patio lights"
```

### With NuCore Backend (eisy)

```shell
python -m intent_handler.run_intent_runtime \
  --intent-dir src/intent_handler_directory \
  --runtime-config src/intent_handler/runtime_assets/nucore_runtime.example.json \
  --backend-api-classpath iox.IoXWrapper \
  --backend-api-base-url https://192.168.6.134 \
  --backend-api-username admin \
  --backend-api-password yourpassword \
  --json-output true
```

### Interactive Mode

Omit `--query` to enter an interactive prompt loop:

```shell
python -m intent_handler.run_intent_runtime \
  --intent-dir src/intent_handler_directory \
  --runtime-config src/intent_handler/runtime_assets/nucore_runtime.example.json \
  --backend-api-classpath iox.IoXWrapper \
  --backend-api-base-url https://192.168.6.134 \
  --backend-api-username admin \
  --backend-api-password yourpassword
```

### Streaming Output

Streaming is always enabled from runtime profiles and no CLI switch is required.

```shell
python -m intent_handler.run_intent_runtime \
  --runtime-config src/intent_handler/runtime_assets/nucore_runtime.example.json \
  --query "What devices are in the master bedroom?"
```

### Secrets File

Use `--secrets_file` to provide API keys as key/value pairs. These values are
loaded into a dict and passed to provider dispatch as the environment source.

The file must be valid JSON with a single top-level object. Each property name
is a secret name and each value is the string to use for lookup. Do not use
shell syntax, comments, or nested structures.

Example `secrets.json`:

```json
{
  "OPENAI_API_KEY": "...",
  "ANTHROPIC_API_KEY": "...",
  "GEMINI_API_KEY": "...",
  "XAI_API_KEY": "...",
  "LLAMACPP_API_KEY": "..."
}
```

Format rules:

- Top level must be a JSON object.
- Keys should be environment-style secret names such as `OPENAI_API_KEY`.
- Values should be strings.
- Duplicate or alias keys are allowed if you want to point multiple names at the same secret value.

Usage:

```shell
python -m intent_handler.run_intent_runtime \
  --runtime-config src/intent_handler/runtime_assets/nucore_runtime.example.json \
  --secrets_file /path/to/secrets.json \
  --query "Turn on the patio lights"
```

### Logging

The runtime now supports centralized, flexible logging for both development and production use.

#### Runtime Logging Flags

```shell
python -m intent_handler.run_intent_runtime \
  --runtime-config src/intent_handler/runtime_assets/nucore_runtime.example.json \
  --log-level DEBUG \
  --log-file logs/intent-runtime.log
```

Use JSON logs for ingestion by external tools:

```shell
python -m intent_handler.run_intent_runtime \
  --runtime-config src/intent_handler/runtime_assets/nucore_runtime.example.json \
  --log-json \
  --log-file logs/intent-runtime.json.log
```

Disable console logs (for quiet batch or service environments):

```shell
python -m intent_handler.run_intent_runtime \
  --runtime-config src/intent_handler/runtime_assets/nucore_runtime.example.json \
  --no-log-console \
  --log-file logs/intent-runtime.log
```

#### Logging Environment Variables

- `NUCORE_LOG_LEVEL` (default: `INFO`)
- `NUCORE_LOG_JSON` (`true`/`false`, default: `false`)
- `NUCORE_LOG_FILE` (optional file path)
- `NUCORE_LOG_CONSOLE` (`true`/`false`, default: `true`)

#### Logger Usage in Code

```python
from utils import configure_logging, get_logger

configure_logging(level="INFO")
logger = get_logger(__name__)
logger.info("runtime started")
```

### Full CLI Reference

| Flag | Description |
|---|---|
| `--intent-dir` | Path to intent handler directory (default: `src/intent_handler_directory`) |
| `--runtime-config` | Required path to JSON with top-level `nucore_runtime` |
| `--secrets_file` | Optional JSON file of secret key/value pairs passed into provider client key resolution |
| `--query` | Single query mode; omit for interactive loop |
| `--backend-api-classpath` | Python class path for backend API (e.g. `iox.IoXWrapper`) |
| `--backend-api-base-url` | Base URL for backend API |
| `--backend-api-username` | Backend API username |
| `--backend-api-password` | Backend API password |
| `--json-output` | Enable JSON output mode for backend API |
| `--prompt_type` | Prompt variant to use (e.g. `shared-features`) |
| `--log-level` | Logging level override: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `--log-file` | Optional rotating log file path |
| `--log-json` | Emit logs in JSON format |
| `--no-log-console` | Disable console logging |

## Supported Providers

| Provider | Alias | Env Var |
|---|---|---|
| Anthropic Claude | `claude`, `anthropic` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai` | `OPENAI_API_KEY` |
| Google Gemini | `gemini`, `google` | `GEMINI_API_KEY` |
| xAI Grok | `grok`, `xai` | `XAI_API_KEY` |
| llama.cpp (local) | `llama.cpp`, `llamacpp` | `LLAMACPP_API_KEY` (optional) |

Provider and model settings come from the runtime profile file passed to `--runtime-config`. Profiles use a `provider` field and do not rely on legacy `llm` aliases or `supported_llms` fallback behavior. API keys can be embedded in the profile, supplied via `--secrets_file`, or read from process environment variables.

## Using a Local (Edge) LLM with llama.cpp

### Build llama.cpp

```shell
sudo apt install build-essential cmake clang libomp-dev libcurl4-openssl-dev
```

#### CPU only

```shell
cmake -B build.cpu
cmake --build build.cpu --config release
```

#### Nvidia GPU

```shell
sudo ubuntu-drivers install
sudo apt install nvidia-cuda-toolkit
cmake -B build.cuda -DGGML_CUDA=on
cmake --build build.cuda --config release
```

### Start the Server

```shell
build.cuda/bin/llama-server \
  -m /path/to/model.gguf \
  -c 64000 --port 8013 --host 0.0.0.0 \
  -t 15 --n-gpu-layers 50 --batch-size 8192
```

### Connect the Runtime to llama.cpp

```shell
python -m intent_handler.run_intent_runtime \
  --runtime-config /path/to/nucore_runtime.json \
  --backend-api-classpath iox.IoXWrapper \
  --backend-api-base-url https://192.168.6.134 \
  --backend-api-username admin \
  --backend-api-password yourpassword
```

Runtime profile for llama.cpp (`--runtime-config` target):

```json
{
  "nucore_runtime": {
    "default": {
      "provider": "llama.cpp",
      "model": "qwen3-instruct",
      "url": "http://192.168.6.113:8013/v1",
      "max_turns": 20,
      "temperature": 0.2,
      "max_tokens": 32000
    }
  }
}
```

## Hardware

Tested with [eisy](https://www.universal-devices.com/product/eisy-home-r2/).

## Further Documentation

- Intent handler architecture: `src/intent_handler/README.md`
- Adding new intents: `src/intent_handler_directory/README.md`
- Directory monitor API (subscribe/start/stop/poll): `src/intent_handler/README.md#directory-monitoring`

