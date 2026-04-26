from __future__ import annotations

from typing import Any

from intent_handler import BaseIntentHandler, IntentHandlerResult


class TranslateAgentOutputIntentHandler(BaseIntentHandler):
    async def get_prompt_runtime_replacements(
        self,
        query,
        *,
        dependency_outputs: IntentHandlerResult | None = None,
        framework_context=None,
        route_result=None,
    ):
        return {}

    async def handle(
        self,
        query,
        *,
        route_result=None,
        framework_context: str | None = None,
        dependency_outputs: IntentHandlerResult | str | dict[str, Any] | None = None,
    ):

        messages = await self.build_messages(
            query,
            dependency_outputs=dependency_outputs,
            framework_context=framework_context,
            route_result=route_result,
        )
        response = await self.call_llm(messages=messages)
        response.set_route_result(route_result=route_result)
        return response