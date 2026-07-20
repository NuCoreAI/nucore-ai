from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .openai_compatible_adapter import OpenAICompatibleAdapter


class LlamaCppAdapter(OpenAICompatibleAdapter):
    """LLM adapter for llama.cpp's OpenAI-compatible ``/v1/chat/completions`` server.

    Inherits request/response handling from :class:`OpenAIAdapter` (via
    :class:`OpenAICompatibleAdapter`) unchanged. Only the base_url
    normalisation (llama.cpp is often pointed at a bare host or a full
    ``/chat/completions`` path rather than ``/v1``) and a couple of
    server-specific quirks (no auth required, no strict ``response_format``
    support, a default model label) differ from stock OpenAI behaviour.
    """

    provider_name = "llama.cpp"

    _forward_temperature_and_max_tokens = True
    _supports_response_format = False

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        # llama.cpp servers typically run without authentication.
        super().__init__(api_key=api_key or "no-key", base_url=self._normalize_base_url(base_url))

    async def generate(
        self,
        *,
        messages: list[dict[str, str]],
        config: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        expect_json: bool = False,
    ) -> Any:
        cfg = dict(config or {})
        cfg.setdefault("model", "llama.cpp")
        return await super().generate(messages=messages, config=cfg, tools=tools, expect_json=expect_json)

    @staticmethod
    def _normalize_base_url(base_url: str | None) -> str | None:
        # The OpenAI SDK expects a base_url ending in /v1 (it appends
        # /chat/completions itself), but llama.cpp is often configured with a
        # bare host or the full /chat/completions path. Normalize both.
        if not base_url:
            return None

        text = str(base_url).strip()
        if not text:
            return None

        parts = urlsplit(text)
        path = (parts.path or "").rstrip("/")

        if path.endswith("/v1/chat/completions"):
            path = path[: -len("/chat/completions")]
        elif path.endswith("/chat/completions"):
            path = path[: -len("/chat/completions")]

        if path in {"", "/"}:
            path = "/v1"

        return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))
