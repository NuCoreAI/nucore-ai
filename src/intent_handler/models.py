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
    resolved_query: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=False)
class IntentHandlerResult:
    intent: str
    output: Any
    route_result: RouteResult | None = None
    tool_result: list[Any] | None = None

    def set_output(self, output: Any):
        self.output = output

    def add_tool_result(self, tool_result: Any):
        if tool_result is None:
            return
        if self.tool_result is None:    
            self.tool_result = []
        self.tool_result.append(tool_result)

    def set_route_result(self, route_result: RouteResult | None = None):
        self.route_result=route_result 

    def get_text_output(self) -> str | None:
        if self.tool_result:
            return self.tool_result if isinstance(self.tool_result, str) else str(self.tool_result) 

        if isinstance(self.output, dict):
            text = self.output.get("text", None)
            if isinstance(text, str) and text.strip():
                return text
            
            content = self.output.get("content", None)
            if isinstance(content, str) and content.strip():
                return content

        return None
    
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


@dataclass
class ConversationTurn:
    query: str
    response: str


@dataclass
class ConversationHistory:
    turns: list[ConversationTurn] = field(default_factory=list)
    max_turns: int = 20

    def append(self, query: str, response: str) -> None:
        self.turns.append(ConversationTurn(query=query, response=response))
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns:]

    def recent(self, n: int | None = None) -> list[ConversationTurn]:
        if n is None:
            return list(self.turns)
        return self.turns[-n:]