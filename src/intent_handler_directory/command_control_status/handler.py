from __future__ import annotations
from typing import Any

from intent_handler import BaseIntentHandler, IntentHandlerResult
from rag import RAGData
from utils import get_logger


logger = get_logger(__name__)


class CommandControlStatusIntentHandler(BaseIntentHandler):
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
        provider = self.get_effective_provider()

        messages = await self.build_messages(
            query,
            dependency_outputs=dependency_outputs,
            framework_context=framework_context,
            route_result=route_result,
        )
        response = await self.call_llm(messages=messages)
        if not response:
            return None
        tools = response.get_tool_calls()
        if tools:
            for tool in tools:
                if tool.name == "tool_command_control":
                    result = await self._process_command_control_tool(tool)
                elif tool.name == "tool_real_time_status":
                    result = await self._process_real_time_status_tool(tool)
                else:
                    result = f"Unknown tool called: {tool.name}"
                response.add_tool_result(tool_result=result) 

        response.set_route_result(route_result=route_result)
        return response


    async def _process_command_control_tool(self, tool):
        if tool is None or tool.args is None:
            return "Invalid tool call: missing arguments"

        if self.nucore_interface is None :
            return "NuCore interface/backend not available"
    
        try:
            # Add your command control tool processing logic here
            return await self.nucore_interface.send_commands(tool.args)

        except Exception as e:
            return f"Error processing command control tool: {str(e)}"


    async def _process_real_time_status_tool(self, tool):
        if tool is None or tool.args is None:
            return "Invalid tool call: missing arguments"

        if self.nucore_interface is None :
            return "NuCore interface/backend not available"

        prop_query= tool.args 
        if not prop_query or len(prop_query) == 0:
            logger.warning("No property query provided")
            return None
        try:
            if isinstance(prop_query[0], list): 
                prop_query = prop_query[0]
        except Exception as e:
            pass
        texts = [] 
        for property in prop_query:
            # Process the property query
            device_id = property.get('device') or property.get('device_id')
            if not device_id:
                logger.warning("No device ID provided for property query", extra={"property": property})
                continue
            properties = await self.nucore_interface.get_properties(device_id)
            if not properties:
                logger.warning("No properties found for device", extra={"device_id": device_id})
                continue
            prop_id = property.get('property') or property.get('property_id')
            device_name = self.nucore_interface.get_device_name(device_id)
            if not device_name:
                device_name = device_id
            if prop_id:
                prop = properties.get(prop_id)
                if prop:
                    texts.append(f"{device_name}: {prop.formatted if prop.formatted else prop.value}")
                else:
                    texts.append(f"Property {prop_id} not found for device {device_name}")
                    logger.warning("Property not found for device", extra={"property_id": prop_id, "device_name": device_name})
            else:
                logger.warning("No property ID provided for device", extra={"device_name": device_name})
        return texts