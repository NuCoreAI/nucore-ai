from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
class ToolResult:
    call_id: str
    name: str
    result: Any
    is_error: bool = False


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_parallel_calls: bool = False
    supports_streaming_tool_args: bool = False
    requires_tool_result_id: bool = True
    supports_strict_json_schema: bool = True
    tool_choice_mode: str = "auto"
    max_tools_per_request: int = 128


@dataclass
class ToolLoopResult:
    final_text: str
    tool_results: list[ToolResult] = field(default_factory=list)
    raw_responses: list[Any] = field(default_factory=list)
    steps: int = 0