from __future__ import annotations

from .openai_compatible_adapter import OpenAICompatibleAdapter


class GrokAdapter(OpenAICompatibleAdapter):
    provider_name = "grok"
