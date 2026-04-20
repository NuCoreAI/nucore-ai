from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dataclasses import dataclass, field
from typing import Any
from adapters import ToolCall

@dataclass(frozen=True)
class IntentDefinition:
    name: str
    directory: Path
    config_path: Path
    prompt_content: str
    handler_path: Path
    description: str
    handler_class: str | None = None
    previous_dependencies: list[str] = field(default_factory=list)
    routing_examples: list[str] = field(default_factory=list)
    router_hints: list[str] = field(default_factory=list)
    llm_config: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteResult:
    intent: str
    confidence: float | None = None
    notes: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=False)
class IntentHandlerResult:
    intent: str
    output: Any
    route_result: RouteResult | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def set_output(self, output: Any):
        self.output = output

    def set_metadata(self, metadata: dict[str, Any] | None = None, route_result: RouteResult | None = None):
            self.route_result=route_result 
            self.metadata=metadata
    
    def get_tool_calls(self) -> list[ToolCall]:
        tool_calls: list[ToolCall] = []
        if self.output is None or not isinstance(self.output, dict):
            return tool_calls 
        tools = self.output.get("tool_calls")
        if isinstance(tools, list):
            for tool in tools:
                if isinstance(tool, dict) and "name" in tool:
                    args = {}
                    try:
                        args = tool.get("input", {})
                        if "args" in args:
                            args = args["args"]
                    except Exception as e:
                        args = {}
                    tool_calls.append(ToolCall(call_id=tool.get("id", ""), name=tool["name"], args=args, provider=tool.get("provider", ""), raw=tool.get("raw", None)))
        return tool_calls