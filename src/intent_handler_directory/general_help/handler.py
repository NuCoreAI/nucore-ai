from __future__ import annotations

import json

from intent_handler import BaseIntentHandler


class GeneralHelpIntentHandler(BaseIntentHandler):
    def get_prompt_runtime_replacements(self, query, *, framework_context=None, route_result=None):
        return {}

    async def handle(self, query, *, route_result=None, framework_context=None):
        backend_snapshot = None

        if self.backend_api is not None:
            try:
                nodes = self.backend_api.get_nodes()
                if isinstance(nodes, str):
                    backend_snapshot = nodes[:4000]
                else:
                    backend_snapshot = json.dumps(nodes, indent=2)[:4000]
            except Exception:
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
        return self.as_result(
            response,
            route_result=route_result,
            metadata={"used_backend_snapshot": bool(backend_snapshot)},
        )