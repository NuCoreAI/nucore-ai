from __future__ import annotations

import os
from typing import Any

import httpx


class GeminiAdapter:
    provider_name = "gemini"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
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
        cfg = dict(config or {})
        model = cfg.get("model") or "gemini-1.5-flash"
        api_key = cfg.get("api_key") or self._api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("Gemini API key not provided. Set GEMINI_API_KEY or config.api_key")

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
            payload["generationConfig"]["responseMimeType"] = "application/json"

        url = f"{self._base_url}/v1beta/models/{model}:generateContent?key={api_key}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        return data
