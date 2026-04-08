from __future__ import annotations

from .adapters import (
    BaseToolsAdapter,
    ClaudeToolsAdapter,
    GeminiToolsAdapter,
    GrokToolsAdapter,
    LlamaCppToolsAdapter,
    OpenAIToolsAdapter,
)


def create_tools_adapter(provider: str) -> BaseToolsAdapter:
    normalized = (provider or "").strip().lower()

    if normalized in {"openai", "gpt"}:
        return OpenAIToolsAdapter()
    if normalized in {"llama.cpp", "llamacpp", "llama_cpp", "qwen-local"}:
        return LlamaCppToolsAdapter()
    if normalized in {"claude", "anthropic"}:
        return ClaudeToolsAdapter()
    if normalized in {"gemini", "google"}:
        return GeminiToolsAdapter()
    if normalized in {"grok", "xai", "x.ai"}:
        return GrokToolsAdapter()

    raise ValueError(f"Unsupported tools adapter provider '{provider}'")