from __future__ import annotations

from intent_handler import BaseIntentHandler, IntentHandlerResult
from intent_handler.base import Any


class RoutineAutomationIntentHandler(BaseIntentHandler):
    def get_prompt_runtime_replacements(self, query, *, framework_context=None, route_result=None):
        return {}

    async def handle(self, query, *, route_result=None, framework_context:str=None, dependency_outputs:IntentHandlerResult | str | dict[str, Any] | None = None):
        provider = self.get_effective_provider()

        messages = self.build_messages(
            query,
            framework_context=framework_context,
            route_result=route_result,
        )
        response = await self.call_llm(messages=messages)

        return self.as_result(
            response,
            route_result=route_result,
            metadata={
                "provider": provider,
                "model": self.get_effective_llm_config().get("model"),
                "tools_loaded": self.get_tool_names(),
            },
        )