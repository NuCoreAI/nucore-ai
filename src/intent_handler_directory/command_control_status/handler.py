from __future__ import annotations
from typing import Any

from intent_handler import BaseIntentHandler, IntentHandlerResult
from rag import RAGData


class CommandControlStatusIntentHandler(BaseIntentHandler):
    def get_prompt_runtime_replacements(self, query, *, dependency_outputs:IntentHandlerResult | str | dict[str, Any] | None = None, framework_context=None, route_result=None):

        dout=""
        if isinstance(dependency_outputs, dict):
            for dependency_output in dependency_outputs.values():
                if isinstance(dependency_output, dict):
                    intent = dependency_output.get("intent", "unknown_intent")
                    output = dependency_output.get("output", "")
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
        provider = self.get_effective_provider()

        messages = self.build_messages(
            query,
            dependency_outputs=dependency_outputs,
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
