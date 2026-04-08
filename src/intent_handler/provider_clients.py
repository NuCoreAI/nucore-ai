from __future__ import annotations

import json
import os
from typing import Any

import httpx
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI


class OpenAIProviderClient:
    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def generate(
        self,
        *,
        messages: list[dict[str, str]],
        config: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        expect_json: bool = False,
    ) -> Any:
        cfg = dict(config or {})
        model = cfg.get("model") or "gpt-4.1-mini"
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        if "temperature" in cfg:
            kwargs["temperature"] = cfg["temperature"]
        if "max_tokens" in cfg:
            kwargs["max_tokens"] = cfg["max_tokens"]
        if expect_json:
            kwargs["response_format"] = {"type": "json_object"}

        response = await self._client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        tool_calls = []
        for call in message.tool_calls or []:
            tool_calls.append(
                {
                    "id": call.id,
                    "type": call.type,
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
            )

        return {
            "content": message.content or "",
            "tool_calls": tool_calls,
            "raw": response.model_dump(),
        }


class AnthropicProviderClient:
    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        self._client = AsyncAnthropic(api_key=api_key, base_url=base_url)

    async def generate(
        self,
        *,
        messages: list[dict[str, str]],
        config: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        expect_json: bool = False,
    ) -> Any:
        cfg = dict(config or {})
        model = cfg.get("model") or "claude-sonnet-4-20250514"

        system_parts: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "system":
                system_parts.append(content)
                continue
            anthropic_role = "assistant" if role == "assistant" else "user"
            anthropic_messages.append({"role": anthropic_role, "content": content})

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": int(cfg.get("max_tokens", 4096)),
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)
        if tools:
            kwargs["tools"] = tools
        if "temperature" in cfg:
            kwargs["temperature"] = cfg["temperature"]

        response = await self._client.messages.create(**kwargs)
        content = response.content
        text_parts = [block.text for block in content if getattr(block, "type", "") == "text"]

        return {
            "content": [block.model_dump() for block in content],
            "text": "\n".join(text_parts),
            "raw": response.model_dump(),
        }


class OpenAICompatibleProviderClient(OpenAIProviderClient):
    pass


class GeminiProviderClient:
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


def build_provider_clients_from_runtime_config(
    runtime_config: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    env_map = env or dict(os.environ)
    supported_llms = dict(runtime_config.get("supported_llms", {}))

    clients: dict[str, Any] = {}
    for _, llm_cfg in supported_llms.items():
        if not isinstance(llm_cfg, dict):
            continue
        provider = str(llm_cfg.get("provider") or llm_cfg.get("llm") or "").strip().lower()
        base_url = llm_cfg.get("url")
        params = llm_cfg.get("params", {}) if isinstance(llm_cfg.get("params"), dict) else {}
        api_key = llm_cfg.get("api_key") or params.get("api_key")

        if provider == "openai":
            key = api_key or env_map.get("OPENAI_API_KEY")
            if key:
                clients["openai"] = OpenAIProviderClient(api_key=key, base_url=base_url)
        elif provider in {"claude", "anthropic"}:
            key = api_key or env_map.get("ANTHROPIC_API_KEY")
            if key:
                clients["claude"] = AnthropicProviderClient(api_key=key, base_url=base_url)
        elif provider in {"grok", "xai", "x.ai"}:
            key = api_key or env_map.get("XAI_API_KEY") or env_map.get("GROK_API_KEY")
            if key:
                clients["grok"] = OpenAICompatibleProviderClient(api_key=key, base_url=base_url)
        elif provider in {"llama.cpp", "llamacpp", "llama_cpp"}:
            key = api_key or env_map.get("LLAMACPP_API_KEY") or "no-key"
            clients["llama.cpp"] = OpenAICompatibleProviderClient(api_key=key, base_url=base_url)
        elif provider in {"gemini", "google"}:
            key = api_key or env_map.get("GEMINI_API_KEY")
            if key:
                clients["gemini"] = GeminiProviderClient(api_key=key, base_url=base_url)

    return clients
