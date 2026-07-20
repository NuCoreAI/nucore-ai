from __future__ import annotations

from .openai_compatible_adapter import OpenAICompatibleAdapter


class GrokAdapter(OpenAICompatibleAdapter):
    """LLM adapter for xAI Grok models.

    Grok exposes an OpenAI-compatible chat completions API, so this class
    inherits all behaviour from :class:`OpenAICompatibleAdapter` and simply
    sets the provider label.  Point ``base_url`` at ``https://api.x.ai/v1``
    and supply an xAI API key when constructing the adapter.
    """

    provider_name = "grok"
