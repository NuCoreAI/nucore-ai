# NuCoreAI Platform! 

## Goal

This library goal is to convert a user query written in natural language to commands, queries, and programs in any NuCore enabled platform (currently eisy).

## Quick start

Installation:

```shell
git clone https://github.com/NuCoreAI/nucore-ai.git
```

## llama.cpp compile and build
1. Download llama.cpp
2. Go to the directory and do as per one of the options below:

### No GPU
```shell
cmake -B build.blis -DGGML_BLAS=on -DGGML_BLAS_VENDOR=FLAME
```
followed by
```shell
cmake --build build.blis --conifg release
```
This will install llama.cpp binaries in build.blis directory local to llama.cpp installation. The reason we are using build.blis directory is that you may want to experiment with the GPU version

### Nvidia GPU
On Ubuntu:
```shell
sudo ubuntu-drivers install
sudo apt install nvidia-utils-{latest version}
sudo apt install nvidia-cuda-toolkit
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
You are going to be using a finetuned version of Qwen2.5-Coder-7B [here](https://huggingface.co/mkohanim/qwen2.5-coder-nucore-7b)

## Testing
For testing, you can either use a live eisy or use example profiles/nodes [here](https://github.com/NuCoreAI/ai-workflow)

## Documentation
The code is very well documented but we have not yet made and official documentation. 