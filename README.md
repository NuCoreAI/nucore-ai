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

### Minimal (no backend)

```shell
python -m intent_handler.run_intent_runtime \
  --intent-dir src/intent_handler_directory \
  --provider claude \
  --api-key sk-ant-api03-... \
  --query "Turn on the patio lights"
```

### With NuCore Backend (eisy)

```shell
python -m intent_handler.run_intent_runtime \
  --intent-dir src/intent_handler_directory \
  --provider claude \
  --api-key sk-ant-api03-... \
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
  --provider claude \
  --api-key sk-ant-api03-... \
  --backend-api-classpath iox.IoXWrapper \
  --backend-api-base-url https://192.168.6.134 \
  --backend-api-username admin \
  --backend-api-password yourpassword
```

### Streaming Output

Add `--stream` to print tokens as they are generated (supported for all providers):

```shell
python -m intent_handler.run_intent_runtime \
  --stream \
  --provider claude \
  --api-key sk-ant-api03-... \
  --query "What devices are in the master bedroom?"
```

### Logging

The runtime now supports centralized, flexible logging for both development and production use.

#### Runtime Logging Flags

```shell
python -m intent_handler.run_intent_runtime \
  --provider claude \
  --api-key sk-ant-api03-... \
  --log-level DEBUG \
  --log-file logs/intent-runtime.log
```

Use JSON logs for ingestion by external tools:

```shell
python -m intent_handler.run_intent_runtime \
  --provider claude \
  --api-key sk-ant-api03-... \
  --log-json \
  --log-file logs/intent-runtime.json.log
```

Disable console logs (for quiet batch or service environments):

```shell
python -m intent_handler.run_intent_runtime \
  --provider claude \
  --api-key sk-ant-api03-... \
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
| `--runtime-config` | Path to `runtime_config.json` |
| `--query` | Single query mode; omit for interactive loop |
| `--provider` | LLM provider override: `claude`, `openai`, `gemini`, `grok`, `llama.cpp` |
| `--model` | Model name override for selected provider |
| `--api-key` | API key override for selected provider |
| `--stream` | Stream tokens to stdout as they arrive |
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

## Supported LLM Providers

| Provider | Alias | Env Var |
|---|---|---|
| Anthropic Claude | `claude`, `anthropic` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai` | `OPENAI_API_KEY` |
| Google Gemini | `gemini`, `google` | `GEMINI_API_KEY` |
| xAI Grok | `grok`, `xai` | `XAI_API_KEY` |
| llama.cpp (local) | `llama.cpp`, `llamacpp` | `LLAMACPP_API_KEY` (optional) |

API keys can be set via environment variables or passed directly via `--api-key`. Provider selection can also be configured in `src/intent_handler/runtime_assets/runtime_config.json`.

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
  --provider llama.cpp \
  --backend-api-classpath iox.IoXWrapper \
  --backend-api-base-url https://192.168.6.134 \
  --backend-api-username admin \
  --backend-api-password yourpassword
```

Runtime config for llama.cpp (`src/intent_handler/runtime_assets/runtime_config.json`):

```json
{
  "supported_llms": {
    "local": {
      "provider": "llama.cpp",
      "model": "qwen3-instruct",
      "url": "http://192.168.6.113:8013/v1"
    }
  },
  "default_llm": "local"
}
```

## Hardware

Tested with [eisy](https://www.universal-devices.com/product/eisy-home-r2/).

## Further Documentation

- Intent handler architecture: `src/intent_handler/README.md`
- Adding new intents: `src/intent_handler_directory/README.md`
- Directory monitor API (subscribe/start/stop/poll): `src/intent_handler/README.md#directory-monitoring`

