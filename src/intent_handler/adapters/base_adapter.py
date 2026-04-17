
import json
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Any
from dataclasses import dataclass, field

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    json_schema: dict[str, Any]
    strict: bool = True

@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    args: dict[str, Any]
    provider: str
    raw: Any = None

@dataclass(frozen=True)
class ProviderCapabilities:
    supports_parallel_calls: bool = False
    supports_streaming_tool_args: bool = False
    requires_tool_result_id: bool = True
    supports_strict_json_schema: bool = True
    tool_choice_mode: str = "auto"
    max_tools_per_request: int = 128

    from dataclasses import dataclass, field


class LLMAdapter(ABC):
    provider_name: str = "unknown"
    @abstractmethod
    async def generate(
        self,
        *,
        messages: list[dict[str, str]],
        config: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        expect_json: bool = False,
    ) -> Any:
        raise NotImplementedError

    @abstractmethod
    def export_tools(self, specs: list[ToolSpec]) -> Any:
        ...
    @abstractmethod
    def parse_tool_calls(self, response: Any) -> list[ToolCall]:
        raise NotImplementedError

    @abstractmethod
    def to_canonical_tools(self, tool_calls: list[ToolCall]) -> list[dict[str, Any]]:
        raise NotImplementedError

    @classmethod
    def tools_spec_from_file(cls, path: str | Path, *, strict: bool = True) -> ToolSpec:
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Tool spec file not found: {file_path}")
        with file_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return cls.tools_spec_from_dict(data, strict=strict)

    @classmethod
    def tools_spec_from_dict(cls, data: dict[str, Any], *, strict: bool = True) -> ToolSpec:
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
    def tools_spec_from_files(cls, paths: list[str | Path], *, strict: bool = True) -> list[ToolSpec]:
        return [cls.tools_spec_from_file(p, strict=strict) for p in paths]