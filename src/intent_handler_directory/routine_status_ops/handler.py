from __future__ import annotations

import json, logging

from intent_handler import BaseIntentHandler, IntentHandlerResult
from intent_handler.base import Any

logger = logging.getLogger(__name__)
def debug(msg):
    logger.debug(f"[PROFILE FORMAT ERROR] {msg}")


class RoutineStatusOpsIntentHandler(BaseIntentHandler):
    async def get_prompt_runtime_replacements(self, query, *, dependency_outputs:IntentHandlerResult| None = None, framework_context=None, route_result=None):
        routines_runtime = "No routine runtime information available." 

        if dependency_outputs is not None and isinstance(dependency_outputs, dict):
            dependency_outputs=dependency_outputs.get("routine_filter", dependency_outputs)
            if isinstance(dependency_outputs, IntentHandlerResult):
                routines_runtime = f"```json\n{json.dumps(dependency_outputs.output, indent=2)}\n```"
            elif isinstance(dependency_outputs, str):
                routines_runtime = dependency_outputs
            elif isinstance(dependency_outputs, dict):
                routines_runtime = f"```json\n{json.dumps(dependency_outputs, indent=2)}\n```"
        return {
            "nucore_routines_runtime": f"{routines_runtime if routines_runtime else 'No routine runtime information available.'}",
        }

    async def handle(self, query, *, route_result=None, framework_context:str=None, dependency_outputs:IntentHandlerResult | str | dict[str, Any] | None = None):
        messages = await self.build_messages(
            query,
            framework_context=framework_context,
            route_result=route_result,
            dependency_outputs=dependency_outputs,
        )
        response = await self.call_llm(messages=messages)
        tool_calls = response.get_tool_calls()
        out = []
        if tool_calls and len(tool_calls) > 0:
            for tool_call in tool_calls:
                tool_call = tool_call.args
                if not tool_call:
                    continue
                for tool in tool_call:
                    routine_id = tool.get("id", None)
                    operation = tool.get("operation", None)
                    if routine_id is None or operation is None:
                        debug(f"Tool call missing 'id' or 'operation' field: {tool}")
                        continue
                    rc = await self.nucore_interface.routine_ops(routine_id=routine_id, operation=operation)
                    if rc is not None:
                        out.append(rc)
            response.output = out if len(out) > 0 else "No routine status operations found in the intent."
        else:
            debug("No tool calls found in the response.")
            response.output = out if len(out) > 0 else "No routine status operations found in the intent." 

        response.set_route_result(route_result=route_result)
        return response
