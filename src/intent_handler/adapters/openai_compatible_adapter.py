from __future__ import annotations

from .openai_adapter import OpenAIAdapter


class OpenAICompatibleAdapter(OpenAIAdapter):
    provider_name = "openai-compatible"
