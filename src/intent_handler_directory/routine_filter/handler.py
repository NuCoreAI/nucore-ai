from __future__ import annotations
import logging
from typing import Any
from intent_handler import BaseIntentHandler, IntentHandlerResult
import json

logger = logging.getLogger(__name__)
def debug(msg):
    logger.debug(f"[PROFILE FORMAT ERROR] {msg}")

class RoutineFilterIntentHandler(BaseIntentHandler):
    def get_prompt_runtime_replacements(self, query, *, dependency_outputs:IntentHandlerResult| None = None, framework_context=None, route_result=None):
        
        if self.nucore_interface is not None:
            try:
                routines_database = self.nucore_interface.get_all_routines_summary()
                if isinstance(routines_database, str):
                    routines_database = routines_database
                elif routines_database is not None:
                    routines_database = json.dumps(routines_database, indent=2)
            except Exception:
                routines_database = ""

        return {
            "routines_database": f"```json\n{routines_database}\n```",
        }

    async def handle(self, query, *, route_result=None, framework_context:str=None, dependency_outputs:IntentHandlerResult | str | dict[str, Any] | None = None):
        messages = self.build_messages(
            query,
            dependency_outputs=dependency_outputs,
            framework_context=framework_context,
            route_result=route_result,
        )

        response = await self.call_llm(messages=messages, expect_json=True)
        if isinstance(response, IntentHandlerResult):
            tools = response.get_tool_calls()
            if not tools or len(tools) == 0:
                debug("No tool calls found in the response.")
                response.set_route_result(route_result=route_result)
                return response
        
            routines = self._get_routine_summary_from_candidates(tools[0])
            if not routines: #or len(rag_docs['documents']) == 0:
                debug("No matched routines found for the candidates.")
                response.set_route_result(route_result=route_result)
                return response

            response.output = routines
    
        response.set_route_result(route_result=route_result)
        return response

    def _get_routine_summary_from_candidates(self, tool: dict) -> list[dict[str, Any]]:
        """
        Get RAGData for the matched devices in the intent.
        
        :param tool_call: Dictionary representing the tool call from the LLM response
        :return: RAGData object containing only the matched devices
        """

        score_threshold = self.config.get("threshold", 0.80)

        out=[]
        routines = tool.args
        for r in routines:
            if float(r.get('score', 0)) >= score_threshold:
                try:
                    routine = self.nucore_interface.get_routine_summary(r['routine_id'])
                    out.append(routine)
                except Exception as ex:
                    pass
        return out
        
