from __future__ import annotations

import json

from intent_handler import BaseIntentHandler, IntentHandlerResult
from intent_handler.base import Any


class RoutineStatusOpsIntentHandler(BaseIntentHandler):
    def get_prompt_runtime_replacements(self, query, *, dependency_outputs:IntentHandlerResult| None = None, framework_context=None, route_result=None):
        routines_runtime = ""

        if self.nucore_interface is not None:
            try:
                routines = self.nucore_interface.get_all_routines_summary()
                if isinstance(routines, str):
                    routines_runtime = routines
                elif routines is not None:
                    routines_runtime = json.dumps(routines, indent=2)
            except Exception:
                routines_runtime = ""

        return {
            "nucore_routines_runtime": routines_runtime,
        }

    async def handle(self, query, *, route_result=None, framework_context:str=None, dependency_outputs:IntentHandlerResult | str | dict[str, Any] | None = None):
        messages = self.build_messages(
            query,
            framework_context=framework_context,
            route_result=route_result,
            dependency_outputs=dependency_outputs,
        )
        response = await self.call_llm(messages=messages)
        response.set_route_result(route_result=route_result)
        return response
