from __future__ import annotations

import json

from intent_handler import BaseIntentHandler, IntentHandlerResult
from intent_handler.base import Any


class GeneralHelpIntentHandler(BaseIntentHandler):
    def get_prompt_runtime_replacements(self, query, *, dependency_outputs:IntentHandlerResult| None = None, framework_context=None, route_result=None):
        self.nucore_interface._refresh_device_structure() # ensure we have the latest device structure before handling the intent   
        return {
            "<<device_database>>": self.nucore_interface.summary_rags
        }

    async def handle(self, query, *, route_result=None, framework_context:str=None, dependency_outputs:IntentHandlerResult | str | dict[str, Any] | None = None):

        messages = self.build_messages(
            query,
            framework_context=framework_context,
            route_result=route_result,
        )
        response = await self.call_llm(messages=messages)
        response.set_route_result(route_result=route_result)
        return response