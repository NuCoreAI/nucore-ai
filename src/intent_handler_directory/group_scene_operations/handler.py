from __future__ import annotations

from intent_handler import BaseIntentHandler, IntentHandlerResult
from intent_handler.base import Any
from rag import RAGData


class GroupSceneOperationsIntentHandler(BaseIntentHandler):
    async def get_prompt_runtime_replacements(self, query, *, dependency_outputs:IntentHandlerResult| None = None, framework_context=None, route_result=None):
        dout=""
        if isinstance(dependency_outputs, dict):
            for dependency_output in dependency_outputs.values():
                if isinstance(dependency_output, IntentHandlerResult):
                    intent = dependency_output.intent
                    output = dependency_output.output
                    if isinstance(output, str):
                        dout += output + "\n\n"
                    elif isinstance(output, RAGData):
                        for document in output['documents']:
                            dout += document + "\n\n"
        
        out = {
            "<<runtime_device_structure>>": dout
        }
        return out

    async def handle(self, query, *, route_result=None, framework_context:str=None, dependency_outputs:IntentHandlerResult | str | dict[str, Any] | None = None):
        messages = await self.build_messages(
            query,
            dependency_outputs=dependency_outputs,
            framework_context=framework_context,
            route_result=route_result,
        )
        response = await self.call_llm(messages=messages)
        response.set_route_result(route_result=route_result)
        return response

