from __future__ import annotations

import json
import os
from typing import Any

import httpx
from .base_adapter import LLMAdapter, ToolCall, ToolSpec


class GeminiAdapter(LLMAdapter):
    """LLM adapter for Google Gemini models via the REST ``generativelanguage`` API.

    Uses ``httpx`` directly (no official async SDK dependency) so the adapter
    stays lightweight.  Supports both streaming (SSE) and non-streaming
    requests.  When streaming fails (e.g. the endpoint does not expose
    ``streamGenerateContent``), the adapter transparently falls back to a
    standard ``generateContent`` call.
    """

    provider_name = "gemini"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        """Initialise the adapter.

        Args:
            api_key:  Google AI Studio / Vertex AI API key.  Falls back to the
                      ``GEMINI_API_KEY`` environment variable when omitted.
            base_url: API root override (default:
                      ``https://generativelanguage.googleapis.com``).
        """
        self._api_key = api_key
        self._base_url = (base_url or "https://generativelanguage.googleapis.com").rstrip("/")

    async def generate(
        self,
        *,
        messages: list[dict[str, str]],
        config: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        expect_json: bool = False,
    ) -> Any:
        """Send a request to the Gemini ``generateContent`` endpoint.

        Gemini uses ``"model"`` for the assistant role (vs ``"assistant"`` used
        by other providers); this mapping is applied automatically.

        When streaming is requested the adapter calls
        :meth:`_stream_generate_content` and falls back silently on failure.

        Returns a dict with keys:
            - ``content``: joined plain text from all text parts
            - ``tool_calls``: canonical tool_use dicts (may be empty)
            - ``raw``:  full JSON response from the API
        """
        cfg = dict(config or {})
        model = cfg.get("model") or "gemini-1.5-flash"
        api_key = cfg.get("api_key") or self._api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("Gemini API key not provided. Set GEMINI_API_KEY or config.api_key")

        # Translate standard roles to Gemini's "user" / "model" vocabulary.
        contents = []
        for msg in messages:
            role = msg.get("role", "user")
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": msg.get("content", "")}]})

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": cfg.get("temperature", 0.2),
                "maxOutputTokens": int(cfg.get("max_tokens", 4096)),
            },
        }
        if tools:
            payload["tools"] = tools
        if expect_json:
            # Instruct Gemini to return a pure JSON response body.
            payload["generationConfig"]["responseMimeType"] = "application/json"

        stream = bool(cfg.get("stream", False))
        stream_handler = cfg.get("stream_handler")
        if stream and callable(stream_handler):
            try:
                return await self._stream_generate_content(
                    model=model,
                    api_key=str(api_key),
                    payload=payload,
                    stream_handler=stream_handler,
                )
            except Exception:
                # Fall back to non-streaming request for compatibility with endpoints
                # that don't expose streamGenerateContent.
                pass

        url = f"{self._base_url}/v1beta/models/{model}:generateContent?key={api_key}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

        text_parts: list[str] = []
        for candidate in data.get("candidates", []) or []:
            content = candidate.get("content", {})
            for part in content.get("parts", []) or []:
                if "text" in part:
                    text_parts.append(part["text"])

        tool_calls = self.to_canonical_tools(self.parse_tool_calls(data))

        return {
            "content": "".join(text_parts),
            "tool_calls": tool_calls,
            "raw": data,
        }

    async def _stream_generate_content(
        self,
        *,
        model: str,
        api_key: str,
        payload: dict[str, Any],
        stream_handler,
    ) -> Any:
        """Consume a Gemini SSE stream and forward text chunks to ``stream_handler``.

        The endpoint uses ``alt=sse`` to return newline-delimited ``data:`` lines,
        each carrying a JSON-encoded response fragment.  All chunks are retained
        so that tool calls present anywhere in the stream can be parsed after
        the stream closes.

        Returns the same shape as :meth:`generate` (non-streaming path), with
        ``raw.streamed`` set to ``True`` and the list of raw chunks stored in
        ``raw.chunks``.
        """
        url = f"{self._base_url}/v1beta/models/{model}:streamGenerateContent?alt=sse&key={api_key}"
        chunks: list[dict[str, Any]] = []
        text_parts: list[str] = []

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", url, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    # SSE comment lines (keep-alive pings) start with ":".
                    if line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue

                    data_str = line[5:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue

                    chunk_json = json.loads(data_str)
                    chunks.append(chunk_json)

                    for candidate in chunk_json.get("candidates", []) or []:
                        content = candidate.get("content", {})
                        for part in content.get("parts", []) or []:
                            text = part.get("text")
                            if isinstance(text, str) and text:
                                text_parts.append(text)
                                stream_handler(text)

        # Re-assemble a full response structure for the tools adapter to parse.
        combined_response = {"candidates": chunks}
        tool_calls = self.to_canonical_tools(self.parse_tool_calls(combined_response))

        return {
            "content": "".join(text_parts),
            "tool_calls": tool_calls,
            "raw": {
                "streamed": True,
                "chunks": chunks,
            },
        }

    def export_tools(self, specs: list[ToolSpec]) -> list[dict[str, Any]]:
        """Convert :class:`ToolSpec` objects to Gemini ``functionDeclarations`` format.

        Gemini expects a single ``tools`` entry whose ``functionDeclarations``
        key holds a list of function descriptors.
        """
        function_declarations = []
        for spec in specs:
            function_declarations.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.json_schema,
                }
            )
        return [{"functionDeclarations": function_declarations}]

    def parse_tool_calls(self, response: Any) -> list[ToolCall]:
        """Extract ``functionCall`` parts from a Gemini response (or stream chunk list).

        Gemini surfaces tool invocations as parts with either a ``functionCall``
        or ``function_call`` key (both spellings are checked for robustness).
        The ``id`` field is not always present; the function name is used as a
        fallback call identifier.
        """
        calls: list[ToolCall] = []
        if not isinstance(response, dict):
            return calls

        candidates = response.get("candidates", []) or []
        for candidate in candidates:
            content = candidate.get("content", {})
            for part in content.get("parts", []) or []:
                function_call = part.get("functionCall") or part.get("function_call")
                if not function_call:
                    continue
                calls.append(
                    ToolCall(
                        call_id=str(function_call.get("id", function_call.get("name", ""))),
                        name=str(function_call.get("name", "")),
                        args=function_call.get("args", {}) if isinstance(function_call.get("args"), dict) else {},
                        provider=self.provider_name,
                        raw=function_call,
                    )
                )
        return calls

    def to_canonical_tools(self, tool_calls: list[ToolCall]) -> list[dict[str, Any]]:
        """Serialise :class:`ToolCall` objects to the shared canonical tool_use format."""
        return [
            {"type": "tool_use", "id": tc.call_id, "name": tc.name, "input": tc.args}
            for tc in tool_calls
        ]