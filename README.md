# NuCoreAI Platform! 

## Goal

This library goal is to convert a user query written in natural language to commands, queries, and programs in any NuCore enabled platform (currently eisy).

## Quick start

Installation:

```shell
git clone https://github.com/NuCoreAI/nucore-ai.git
```

## Using Frontier LLMs
* Create a directory called ```secrets``` in the root of this project
* Create the following two files in this directory
1. "__init__.py" - an empty file
2. "keys.py" 
In keys.py, put your API KEYs in this format:

```python
OPENAI_API_KEY="sk-proj-xxxx-your-api-key" (for OpenAI)
XAI_API_KEY_SAMPLES="xai-xxxx-your-api-key" (for xAI)
CLAUDE_API_KEY="sk-ant-xxxx-your-api-key" (for Claude)
```

## Using local (edge) LLMs 
### llama.cpp compile and build
1. Download llama.cpp and install prereqs
```shell
sudo apt install build-essential 
sudo apt install cmake
sudo apt install clang
sudo apt install libomp-dev
sudo apt install libcurl4-openssl-dev 

```
2. Go to the directory and do as per one of the options below:

#### No GPU
```shell
cmake -B build.blis -DGGML_BLAS=on -DGGML_BLAS_VENDOR=FLAME
```
followed by
```shell
cmake --build build.blis --conifg release
```
This will install llama.cpp binaries in build.blis directory local to llama.cpp installation. The reason we are using build.blis directory is that you may want to experiment with the GPU version

#### Nvidia GPU
On Ubuntu:
```shell
sudo ubuntu-drivers install
sudo apt install nvidia-utils-{latest version}
sudo apt install nvidia-cuda-toolkit
sudo apt install nvidia-prime (for intel)
```
Now you are ready to build:
```shell
cmake -B build.cuda -DGGML_CUDA=on 
```
followed by
```shell
cmake --build build.cuda --config release
```
If you have x running, you may want to have it release resources. First use nvidia-smi utility to see what's running and how much memory is being used by other things:
```shell
sudo nvidia-smi
```
if anything is running and using memory:
1. Make the prime display point to the integrated one (say intel)
```shell
sudo prime-select intel
```
2. Then, make it on demand
```shell
sudo prime-select on-demand
```
3. Make sure your system sees it:
```shell
nvidia-smi
```

## The Model
[Qwen3-Instruct-4b-Q4M.gguf](https://mygguf.com/models/unsloth_Qwen3-4B-Instruct-2507-GGUF)
Choose Q4M quantization.

### Command
```shell
build.cuda/bin/llama-server -m /home/michel/workspace/nucore/models/qwen3-instruct-4b.q4.gguf  -c 64000 --port 8013 --host 0.0.0.0 -t 15 --n-gpu-layers 50 --batch-size 8192
```

## Testing
1. For now, you will need an [eisy hardware] (https://www.universal-devices.com/product/eisy-home-r2/)
2. Clone this repo anywhere
3. There are three assistant types that use the same codebase:
src/assistant/generic_assistant -> uses local/edge LLM (qwen)
src/assistant/openai_assistant -> uses OpenAI (you need an API Key) 
src/assistant/claude_assistant -> uses Clause (you need an API Key) 

All have the same parameters:

```python
    "--url"             , # The URL to fetch nodes and profiles from the nucore platform",
    "--username"        , # The username to authenticate with the nucore platform",
    "--password"        , # The password to authenticate with the nucore platform",
    "collection_path"   , # The path to the embedding collection db. If not provided, defaults to ~/.nucore_db.
    "--model_url"       , # The URL of the remote model. If provided, this should be a valid URL that responds to OpenAI's API requests. If frontier, use openai, claude, or xai"
    "--model_auth_token", # Optional authentication token for the remote model API (if required by the remote model) to be used in the Authorization header. You are responsible for refreshing the token if needed. This is in case you are hosing your own model in AWS or Runpod, etc. 
    "--embedder_url"    , # Embedder to use.  If nothing provided, then default local embedder will be used.  If a model name is provided, it will be used as the local embedder model downloaded at runtime from hg.  If a URL is provided, it should be a valid URL that responds to OpenAI's API requests."
    "--reranker_url"    , # The URL of the reranker service. If provided, this should be a valid URL that responds to OpenAI's API requests."
    "--prompt_type"     , # The type of prompt to use (e.g., 'per-device', 'shared-features', etc.)
```
### Examples:
1. Local/Edge 
```python
python3 src/assistant/generic_assistant.py\
    --url=http://192.168.6.126:8443 ,\ 
    --username=admin, \ 
    --password=admin, \ 
    --model_url=http://192.168.6.113:8013/v1/chat/completions, \ 
    --prompt_type=per-device
```
2. OpenAI
```python
python3 src/assistant/openai_assistant.py\
    --url=http://192.168.6.126:8443, \ 
    --username=admin, \
    --password=admin, \
    --model_url=openai, \
    --prompt_type=per-device
```

## Documentation
The code is very well documented but we have not yet made and official documentation. 
