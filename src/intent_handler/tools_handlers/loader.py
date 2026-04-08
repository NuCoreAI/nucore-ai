from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ToolSpec

from .adapters import (
    BaseToolsAdapter,
    ClaudeToolsAdapter,
    GeminiToolsAdapter,
    GrokToolsAdapter,
    LlamaCppToolsAdapter,
    OpenAIToolsAdapter,
)



class ToolLoader:
    """
    Loads tool specs from the canonical authoring format (Claude-style) and
    converts them to a provider-agnostic ToolSpec.  Each provider adapter's
    export_tools() then re-wraps the ToolSpec into the format that LLM expects.

    Authoring format  (save as any .json file in your tools directory)
    ----------------------------------------------------------------
    {
      "name": "my_tool",
      "description": "What this tool does.",
      "input_schema": {
        "type": "object",
        "required": ["param_a"],
        "properties": {
          "param_a": { "type": "string", "description": "..." }
        },
        "additionalProperties": false
      }
    }

    Conversion chain
    ----------------
    author JSON  ->  ToolLoader  ->  ToolSpec  ->  adapter.export_tools()

    adapter.export_tools() output per provider
    ------------------------------------------
    openai / grok  :  {type, function: {name, description, parameters, strict}}
    llama.cpp      :  {type, function: {name, description, parameters}}
    gemini         :  [{functionDeclarations: [{name, description, parameters}]}]
    claude         :  {name, description, input_schema}  (round-trip)
    """

    claudeAdapter: ClaudeToolsAdapter = None
    openaiAdapter: OpenAIToolsAdapter = None
    llamaCppAdapter: LlamaCppToolsAdapter = None
    geminiAdapter: GeminiToolsAdapter = None
    grokAdapter: GrokToolsAdapter = None

    @classmethod
    def from_file(cls, path: str | Path, *, strict: bool = True) -> ToolSpec:
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Tool spec file not found: {file_path}")
        with file_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return cls.from_dict(data, strict=strict)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, strict: bool = True) -> ToolSpec:
        if not isinstance(data, dict):
            raise TypeError(f"Expected a dict, got {type(data).__name__}")

        name = data.get("name", "")
        description = data.get("description", "")
        json_schema = data.get("input_schema")

        if not name:
            raise ValueError(f"Tool spec is missing a 'name' field: {data}")
        if not isinstance(json_schema, dict):
            raise ValueError(
                f"Tool spec '{name}' is missing an 'input_schema' object. "
                "All tool files must use the Claude authoring format."
            )

        return ToolSpec(
            name=name,
            description=description,
            json_schema=json_schema,
            strict=strict,
        )

    @classmethod
    def from_files(cls, paths: list[str | Path], *, strict: bool = True) -> list[ToolSpec]:
        return [cls.from_file(p, strict=strict) for p in paths]
    

    @classmethod
    def convert_for_provider_from_file(cls, provider:str, path: str | Path, *, strict: bool = True) -> list[dict[str, Any]]:
        spec = cls.from_file(path, strict=strict)
        if spec is None:
            raise ValueError(f"Failed to load tool spec from {path}")
        adapter = cls.__get_tools_adapter(provider)
        return adapter.export_tools([spec])  
    
    @classmethod
    def convert_for_provider_from_dict(cls, provider:str, data: dict[str, Any], *, strict: bool = True) -> list[dict[str, Any]]:
        spec = cls.from_dict(data, strict=strict)
        if spec is None:
            raise ValueError(f"Failed to load tool spec from data: {data}")
        adapter = cls.__get_tools_adapter(provider)
        return adapter.export_tools([spec])
    
    @classmethod
    def __get_tools_adapter(cls, provider: str) -> BaseToolsAdapter:
        normalized = (provider or "").strip().lower()

        if normalized in {"openai", "gpt"}:
            if cls.openaiAdapter is None:
                cls.openaiAdapter = OpenAIToolsAdapter()
            return cls.openaiAdapter
        if normalized in {"llama.cpp", "llamacpp", "llama_cpp", "qwen-local"}:
            if cls.llamaCppAdapter is None:
                cls.llamaCppAdapter = LlamaCppToolsAdapter()
            return cls.llamaCppAdapter
        if normalized in {"claude", "anthropic"}:
            if cls.claudeAdapter is None:
                cls.claudeAdapter = ClaudeToolsAdapter()
            return cls.claudeAdapter
        if normalized in {"gemini", "google"}:
            if cls.geminiAdapter is None:
                cls.geminiAdapter = GeminiToolsAdapter()
            return cls.geminiAdapter
        if normalized in {"grok", "xai", "x.ai"}:
            if cls.grokAdapter is None:
                cls.grokAdapter = GrokToolsAdapter()
            return cls.grokAdapter

        raise ValueError(f"Unsupported tools adapter provider '{provider}'")
