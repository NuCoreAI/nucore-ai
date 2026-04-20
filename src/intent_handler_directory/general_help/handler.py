from __future__ import annotations

import json

from intent_handler import BaseIntentHandler, IntentHandlerResult
from intent_handler.base import Any


class GeneralHelpIntentHandler(BaseIntentHandler):
    def get_prompt_runtime_replacements(self, query, *, dependency_outputs:IntentHandlerResult| None = None, framework_context=None, route_result=None):
        return {}

    async def handle(self, query, *, route_result=None, framework_context:str=None, dependency_outputs:IntentHandlerResult | str | dict[str, Any] | None = None):
        backend_snapshot = None

        messages = self.build_messages(
            query,
            framework_context=framework_context,
            route_result=route_result,
            extra_user_sections={
                "backend_snapshot": backend_snapshot or "",
            },
        )
        response = await self.call_llm(messages=messages)
        metadata={"used_backend_snapshot": bool(backend_snapshot)},
        response.set_metadata(metadata=metadata, route_result=route_result)
        return response