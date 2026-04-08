from __future__ import annotations

import asyncio
from typing import Any, Protocol

from .adapters import BaseToolsAdapter
from .models import ToolCall, ToolLoopResult, ToolResult
from .registry import ToolRegistry


class ToolCapableClient(Protocol):
    async def generate(self, *, payload: dict[str, Any]) -> Any:
        ...


class ToolLoopEngine:
    def __init__(
        self,
        *,
        adapter: BaseToolsAdapter,
        llm_client: ToolCapableClient,
        registry: ToolRegistry,
        max_steps: int = 6,
        parallel_calls: bool = True,
    ) -> None:
        self.adapter = adapter
        self.llm_client = llm_client
        self.registry = registry
        self.max_steps = max_steps
        self.parallel_calls = parallel_calls

    async def run(
        self,
        *,
        messages: list[dict[str, Any]],
        llm_config: dict[str, Any] | None = None,
    ) -> ToolLoopResult:
        conversation = self.adapter.build_conversation(messages)
        all_tool_results: list[ToolResult] = []
        raw_responses: list[Any] = []

        for step in range(1, self.max_steps + 1):
            payload = self.adapter.request_payload(
                conversation=conversation,
                tool_specs=self.registry.specs(),
                config=llm_config,
            )
            response = await self.llm_client.generate(payload=payload)
            raw_responses.append(response)

            tool_calls = self.adapter.parse_tool_calls(response)
            if not tool_calls:
                final_text = self.adapter.extract_final_text(response)
                return ToolLoopResult(
                    final_text=final_text,
                    tool_results=all_tool_results,
                    raw_responses=raw_responses,
                    steps=step,
                )

            self.adapter.append_model_response(conversation, response)
            tool_results = await self._execute_calls(tool_calls)
            all_tool_results.extend(tool_results)
            self.adapter.append_tool_results(
                conversation,
                tool_calls=tool_calls,
                tool_results=tool_results,
            )

        return ToolLoopResult(
            final_text="",
            tool_results=all_tool_results,
            raw_responses=raw_responses,
            steps=self.max_steps,
        )

    async def _execute_calls(self, tool_calls: list[ToolCall]) -> list[ToolResult]:
        if not self.parallel_calls or not self.adapter.capabilities().supports_parallel_calls:
            results: list[ToolResult] = []
            for call in tool_calls:
                results.append(await self.registry.execute(call))
            return results

        tasks = [self.registry.execute(call) for call in tool_calls]
        return list(await asyncio.gather(*tasks))