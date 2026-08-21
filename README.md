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

## Running the Unified Runtime

The entry point is the unified runtime: one system prompt, one native
tool-calling agentic loop, no router, no per-intent directory dispatch. It
executes user queries directly against a NuCore backend.

Create a runtime profile JSON first (see `src/unified/runtime_config.example.json`).

### Minimal (no backend)

```shell
python -m unified.run_unified_runtime \
  --runtime-config src/unified/runtime_config.example.json \
  --query "Turn on the patio lights"
```

### With NuCore Backend (eisy)

```shell
python -m unified.run_unified_runtime \
  --runtime-config src/unified/runtime_config.example.json \
  --backend-api-classpath iox.IoXWrapper \
  --backend-api-base-url https://192.168.6.134 \
  --backend-api-username admin \
  --backend-api-password yourpassword \
  --json-output true
```

### Interactive Mode

Omit `--query` to enter an interactive prompt loop:

```shell
python -m unified.run_unified_runtime \
  --runtime-config src/unified/runtime_config.example.json \
  --backend-api-classpath iox.IoXWrapper \
  --backend-api-base-url https://192.168.6.134 \
  --backend-api-username admin \
  --backend-api-password yourpassword
```

### WebSocket Server Mode

Pass `--websocket-port` to run as a standalone WebSocket server instead of
`--query`/REPL mode -- no HTTP framework involved (uses the `websockets` package, already
a project dependency). Each connection gets its own session and conversation history;
every received message is treated as a query, and the response streams back over the
same connection.

```shell
python -m unified.run_unified_runtime \
  --runtime-config src/unified/runtime_config.example.json \
  --backend-api-classpath iox.IoXWrapper \
  --backend-api-base-url https://192.168.6.134 \
  --backend-api-username admin \
  --backend-api-password yourpassword \
  --websocket-port 8765
```

This is a lower-level alternative to `eisy_ai`'s FastAPI-based chat server (a separate
sibling project) -- use this mode when a raw `ws://` endpoint is all you need, without
serving a browser UI.

#### TLS (wss://)

Add `--ssl-certfile`/`--ssl-keyfile` (a PEM cert and private key, given together) to
serve `wss://` instead of `ws://` -- needed for clients that always connect over TLS
(e.g. the Eisy UI). Uses Python's standard-library `ssl` module, no extra dependency.

```shell
python -m unified.run_unified_runtime \
  --runtime-config src/unified/runtime_config.example.json \
  --backend-api-classpath iox.IoXWrapper \
  --backend-api-base-url https://192.168.6.134 \
  --backend-api-username admin \
  --backend-api-password yourpassword \
  --websocket-port 8765 \
  --ssl-certfile secrets/cert.pem \
  --ssl-keyfile secrets/key.pem
```

Generate a self-signed pair for local/dev use with:

```shell
openssl req -x509 -newkey rsa:2048 -keyout secrets/key.pem -out secrets/cert.pem \
  -days 825 -nodes -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

`secrets/` is already gitignored. A self-signed cert will trigger a browser warning
unless the client is configured to skip verification.

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
python -m unified.run_unified_runtime \
  --runtime-config src/unified/runtime_config.example.json \
  --secrets_file /path/to/secrets.json \
  --query "Turn on the patio lights"
```

### Logging

The runtime supports centralized, flexible logging for both development and production use.

#### Runtime Logging Flags

```shell
python -m unified.run_unified_runtime \
  --runtime-config src/unified/runtime_config.example.json \
  --log-level DEBUG \
  --log-file logs/unified-runtime.log
```

Use JSON logs for ingestion by external tools:

```shell
python -m unified.run_unified_runtime \
  --runtime-config src/unified/runtime_config.example.json \
  --log-json \
  --log-file logs/unified-runtime.json.log
```

Disable console logs (for quiet batch or service environments):

```shell
python -m unified.run_unified_runtime \
  --runtime-config src/unified/runtime_config.example.json \
  --no-log-console \
  --log-file logs/unified-runtime.log
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
| `--runtime-config` | Required path to JSON with top-level `nucore_runtime` |
| `--secrets_file` | Optional JSON file of secret key/value pairs passed into provider client key resolution |
| `--query` | Single query mode; omit for interactive loop |
| `--websocket-port` | Run as a native WebSocket server on this port instead of `--query`/REPL mode |
| `--ssl-certfile` | PEM cert file; with `--ssl-keyfile`, serves `--websocket-port` over `wss://` |
| `--ssl-keyfile` | PEM private key file; with `--ssl-certfile`, serves `--websocket-port` over `wss://` |
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
python -m unified.run_unified_runtime \
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

## Capabilities

Beyond device/group/routine/variable command-and-control, the unified runtime supports:

- **Diagnostics** -- `start_diagnostics` opens a guided, single-session troubleshooting flow
  (e.g. "my thermostat keeps rebooting"); `run_diagnostic_step` drives it step by step, including
  checking and starting/stopping/restarting core or plugin services.
- **Plan** -- `start_plan`/`run_plan_step` walk a customer through a structured multi-step task
  such as a new device installation, rather than a single command/response turn.
- **User preferences** -- `preference_op`/`list_preferences` store per-user aliases (e.g. naming
  a device or routine) and event subscriptions, persisted across sessions.
- **Plugin management** -- `list_store_plugins`/`list_purchased_plugins`/`list_installed_plugins`
  cover NuCore's plugin marketplace (browse/license/installed state); `get_plugin_capabilities`/
  `call_plugin` let the model extend its own capabilities with a plugin's tools when no
  built-in tool covers a request. `install_plugin`/`buy_plugin`/`delete_plugin` don't complete
  anything themselves -- for security reasons, installing, purchasing, and deleting all happen on
  the web -- each returns a link for the customer to finish there. Starting/stopping/restarting a
  plugin's underlying service goes through the diagnostics flow above, not this one.

See `src/unified/prompt/definitions.md` for the exact tool-selection rules the model follows for
each of these, and `src/unified/README.md` for the tool/handler layout.

## Hardware

Tested with [eisy](https://www.universal-devices.com/product/eisy-home-r2/).

## Further Documentation

- Unified runtime architecture and tool reference: `src/unified/README.md`

