from __future__ import annotations

from .openai_adapter import OpenAIAdapter


class OpenAICompatibleAdapter(OpenAIAdapter):
    """Adapter for providers that expose an OpenAI-compatible chat completions API.

    This is a thin subclass of :class:`OpenAIAdapter` that changes only the
    ``provider_name`` label.  The ``base_url`` passed at construction time
    points the underlying ``AsyncOpenAI`` client at the third-party endpoint
    (e.g. llama.cpp, Ollama, Together AI, Fireworks AI).

    Inherit from this class rather than :class:`OpenAIAdapter` when the
    provider needs additional request/response transformations on top of the
    standard OpenAI wire format.
    """

    provider_name = "openai-compatible"
