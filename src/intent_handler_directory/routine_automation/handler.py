from __future__ import annotations

from intent_handler import BaseIntentHandler, IntentHandlerResult
from intent_handler.base import Any


class RoutineAutomationIntentHandler(BaseIntentHandler):
    def get_prompt_runtime_replacements(self, query, *, dependency_outputs:IntentHandlerResult| None = None, framework_context=None, route_result=None):
        return {}

    async def handle(self, query, *, route_result=None, framework_context:str=None, dependency_outputs:IntentHandlerResult | str | dict[str, Any] | None = None):
        provider = self.get_effective_provider()

        messages = self.build_messages(
            query,
            dependency_outputs=dependency_outputs,
            framework_context=framework_context,
            route_result=route_result,
        )
        response = await self.call_llm(messages=messages)
        response.set_route_result(route_result=route_result)
        tools = response.get_tool_calls()
        if tools:
            for tool in tools:
                if tool.name == "tool_routine_automation":
                    result = await self._process_routine_automation(tool)
                else:
                    result = f"Unknown tool called: {tool.name}"
                response.add_tool_result(tool_result=result) 

        response.set_route_result(route_result=route_result)
        return response

    async def _process_routine_automation(self, tool):
        if tool is None or tool.args is None:
            return "Invalid tool call: missing arguments"

        if self.nucore_interface is None :
            return "NuCore interface/backend not available"
    
        try:
            result = []
            for routine in tool.args:
                result.append(await self.nucore_interface.create_automation_routine(routine))
            return result

        except Exception as e:
            return f"Error processing routine automation tool: {str(e)}"