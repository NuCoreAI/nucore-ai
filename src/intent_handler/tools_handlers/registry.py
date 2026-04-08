from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .models import ToolCall, ToolResult, ToolSpec

ToolCallable = Callable[[dict[str, Any]], Any] | Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass
class RegisteredTool:
    spec: ToolSpec
    fn: ToolCallable


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        *,
        name: str,
        description: str,
        json_schema: dict[str, Any],
        fn: ToolCallable,
        strict: bool = True,
    ) -> None:
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered")

        self._tools[name] = RegisteredTool(
            spec=ToolSpec(name=name, description=description, json_schema=json_schema, strict=strict),
            fn=fn,
        )

    def specs(self) -> list[ToolSpec]:
        return [entry.spec for entry in self._tools.values()]

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> RegisteredTool:
        if name not in self._tools:
            raise KeyError(f"Unknown tool '{name}'")
        return self._tools[name]

    async def execute(self, call: ToolCall) -> ToolResult:
        entry = self.get(call.name)
        args = self._validate_args(entry.spec, call.args)

        try:
            result = entry.fn(args)
            if inspect.isawaitable(result):
                result = await result
            return ToolResult(call_id=call.call_id, name=call.name, result=result, is_error=False)
        except Exception as exc:
            return ToolResult(
                call_id=call.call_id,
                name=call.name,
                result={"error": str(exc), "type": type(exc).__name__},
                is_error=True,
            )

    def _validate_args(self, spec: ToolSpec, args: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(args, dict):
            raise ValueError(f"Arguments for tool '{spec.name}' must be a JSON object")

        schema = spec.json_schema or {}
        required = schema.get("required", [])
        for key in required:
            if key not in args:
                raise ValueError(f"Missing required argument '{key}' for tool '{spec.name}'")

        props = schema.get("properties", {})
        if spec.strict:
            for key in args.keys():
                if key not in props:
                    raise ValueError(f"Unexpected argument '{key}' for tool '{spec.name}'")

        return args