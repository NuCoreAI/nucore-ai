from __future__ import annotations
from typing import Any

from intent_handler import BaseIntentHandler, IntentHandlerResult


class RoutineFilterIntentHandler(BaseIntentHandler):
    def get_prompt_runtime_replacements(self, query, *, framework_context=None, route_result=None):
        return {}

    async def handle(self, query, *, route_result=None, framework_context:str=None, dependency_outputs:IntentHandlerResult | str | dict[str, Any] | None = None):
        messages = self.build_messages(
            query,
            framework_context=framework_context,
            route_result=route_result,
        )
        response = await self.call_llm(messages=messages, expect_json=True)
        return self.as_result(
            response,
            route_result=route_result,
            metadata={"stage": "routine_filter"},
        )
