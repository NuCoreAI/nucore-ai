from __future__ import annotations

from ..models import ProviderCapabilities
from .openai_adapter import OpenAIToolsAdapter


class GrokToolsAdapter(OpenAIToolsAdapter):
    provider_name = "grok"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_parallel_calls=True,
            supports_streaming_tool_args=True,
            requires_tool_result_id=True,
            supports_strict_json_schema=True,
            tool_choice_mode="auto",
        )