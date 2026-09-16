from __future__ import annotations

from typing import Any

from .openai_compatible_adapter import OpenAICompatibleAdapter


class GrokAdapter(OpenAICompatibleAdapter):
    """LLM adapter for xAI Grok models.

    Grok exposes an OpenAI-compatible chat completions API, so this class
    inherits all behaviour from :class:`OpenAICompatibleAdapter` and simply
    sets the provider label.  Point ``base_url`` at ``https://api.x.ai/v1``
    and supply an xAI API key when constructing the adapter.
    """

    provider_name = "grok"

    def _extra_headers(self, cfg: dict[str, Any]) -> dict[str, str] | None:
        """xAI routes repeat requests carrying the same ``x-grok-conv-id`` to
        the same cache-warm server -- without it, prompt caching effectively
        never hits across turns of the same conversation. ``session_id``
        (threaded through from UnifiedRuntime.handle_query's own per-
        conversation session id, via generate()'s config dict) is exactly
        that: stable for the life of one conversation, distinct across
        separate ones. No session_id in cfg (e.g. a call outside the normal
        per-conversation runtime path) means no header -- default routing,
        not an error."""
        session_id = cfg.get("session_id")
        return {"x-grok-conv-id": str(session_id)} if session_id else None
