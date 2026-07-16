"""The genuine multi-turn agentic tool-calling loop.

Replaces the router's pre-planned ``route_plan``/``_execute_route_plan``
(runtime.py) with the pattern every major provider's native tool-calling API
is actually designed for: call a tool, get a result, decide whether to call
another tool or answer -- repeated in one continuous conversation, tools
staying enabled the whole time (unlike ``_synthesize_tool_result_response``,
which makes exactly one tool-call round then a separate call with
``tools=[]``).
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from intent_handler.adapters import LLMAdapter, ToolCall, ToolSpec
from utils import get_logger

logger = get_logger(__name__)

ToolDispatch = Callable[[str, dict[str, Any]], Awaitable[Any]]


class AgenticLoop:
    """Drives one query through repeated generate/tool-execute rounds."""

    def __init__(
        self,
        *,
        llm_client: LLMAdapter,
        tool_specs: list[ToolSpec],
        dispatch: ToolDispatch,
        max_iterations: int = 8,
    ) -> None:
        self.llm_client = llm_client
        self.tool_specs = tool_specs
        self._exported_tools = llm_client.export_tools(tool_specs)
        self.dispatch = dispatch
        self.max_iterations = max_iterations

    @staticmethod
    def _canonical_to_tool_calls(canonical: list[dict[str, Any]]) -> list[ToolCall]:
        return [
            ToolCall(
                call_id=str(tc.get("id", "")),
                name=str(tc.get("name", "")),
                args=tc.get("input") or {},
                provider="",
                raw=tc,
            )
            for tc in canonical
        ]

    async def run(
        self,
        *,
        system_prompt: str,
        history_messages: list[dict[str, Any]],
        user_message: str,
        llm_config: dict[str, Any] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Run the loop for one user turn.

        Returns:
            ``(final_text, new_messages)`` -- the model's final answer, and
            the messages generated this turn (the user turn plus every
            tool-call/tool-result round trip and the final assistant reply),
            for a caller that wants to persist the full turn.
        """
        messages: list[dict[str, Any]] = (
            [{"role": "system", "content": system_prompt}]
            + list(history_messages)
            + [{"role": "user", "content": user_message}]
        )
        new_messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

        for _ in range(self.max_iterations):
            raw_response = await self.llm_client.generate(
                messages=messages,
                config=llm_config,
                tools=self._exported_tools,
            )
            canonical_calls = raw_response.get("tool_calls") or []
            if not canonical_calls:
                text = raw_response.get("text") or raw_response.get("content") or ""
                if not isinstance(text, str):
                    text = str(text)
                new_messages.append({"role": "assistant", "content": text})
                return text, new_messages

            tool_calls = self._canonical_to_tool_calls(canonical_calls)
            tool_results = await asyncio.gather(*[self.dispatch(tc.name, tc.args) for tc in tool_calls])

            round_trip = self.llm_client.build_tool_round_trip_messages(
                raw_response=raw_response,
                tool_calls=tool_calls,
                tool_results=list(tool_results),
            )
            messages.extend(round_trip)
            new_messages.extend(round_trip)

        logger.warning("AgenticLoop hit max_iterations=%d without a final answer", self.max_iterations)
        fallback = (
            "I wasn't able to finish that within the allowed number of steps -- "
            "please try rephrasing or breaking it into smaller requests."
        )
        new_messages.append({"role": "assistant", "content": fallback})
        return fallback, new_messages
