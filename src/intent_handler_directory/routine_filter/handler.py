from __future__ import annotations
from dataclasses import dataclass
import logging
from typing import Any
from intent_handler import BaseIntentHandler, IntentHandlerResult
import json

logger = logging.getLogger(__name__)
def debug(msg):
    logger.debug(f"[PROFILE FORMAT ERROR] {msg}")


class RoutineFilterIntentHandler(BaseIntentHandler):

    all_routines: dict[str, Any] = {}
    condensed_routines: list = [] 
    is_refreshed:bool = False
    
    def get_prompt_runtime_replacements(self, query, *, dependency_outputs:IntentHandlerResult| None = None, framework_context=None, route_result=None):
        if self.nucore_interface is not None:
            self.nucore_interface._refresh_device_structure()
            self._refresh_routines_database()

        return {
            "routines_database": f"```json\n{RoutineFilterIntentHandler.condensed_routines}\n```",
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
            
            for rs in routines:
                if rs is None:
                    debug("Received None routine summary from Nucore interface.")
                elif 'id' not in rs:
                    debug(f"Routine summary missing 'id' field: {rs}")
                else:
                    routine_id = rs['id']
                    full_routine = RoutineFilterIntentHandler.all_routines.get(routine_id, None)
                    if full_routine is None:
                        debug(f"No full routine found for routine ID: {routine_id}")
                    else:
                        rs['routine_logic'] = full_routine

            response.output = routines
    
        response.set_route_result(route_result=route_result)
        return response

    def _refresh_routines_database(self):
        if RoutineFilterIntentHandler.is_refreshed or self.nucore_interface is None:
            return 

        try:
            all_routines = self.nucore_interface.get_all_routines()
            RoutineFilterIntentHandler.is_refreshed = True

            # now go thorugh the list and create both the full and condensed versions of the routines database 
            # codensed version is used for filtering using device names, while the full version is sent to intent handlers for full processing
            for r in all_routines:
                routine = r.get("routine", {})
                routine_id = routine.get("id", "")
                if not routine_id:
                    continue
                condensed_routine = {
                    "id": routine_id, 
                    "name": routine.get("name"),
                    "comment": routine.get("comment"),
                    "device_names": self._get_device_name_list_from_routine(routine) 
                }

                if "invalid" in r:
                    routine["invalid"]=r.get("invalid", False)
                    routine["invalid_reason"]=r.get("error", "")
                    condensed_routine["invalid"]=r.get("invalid", False)
                    condensed_routine["invalid_reason"]=r.get("error", "")
                RoutineFilterIntentHandler.all_routines[f"{routine_id}"] = routine
                RoutineFilterIntentHandler.condensed_routines.append(condensed_routine)

        except Exception as ex:
            pass

    def _get_device_name_list_from_routine(self, routine: dict) -> list[str]: 
        if routine is None:
            return []

        #first check the if section:        
        if_section: list[dict] = routine.get("if", [])
        then_section: list[dict] = routine.get("then", [])
        else_section: list[dict] = routine.get("else", [])
        device_id_list = []
        for condition in if_section:
            if "device" in condition:
                device = condition.get("device", None)
                if device:                    
                    device_id_list.append(device)
        
        for action in then_section:
            if "device" in action:
                device = action.get("device", None)
                if device:
                    device_id_list.append(device)
   
        for action in else_section:
            if "device" in action:
                device = action.get("device", None)
                if device:
                    device_id_list.append(device)

        device_names: list[str] = []
        if self.nucore_interface is not None:
            for device_id in device_id_list:
                try:
                    device_name = self.nucore_interface.get_device_name(device_id)
                    if device_name:
                        device_names.append(device_name)
                except Exception as ex:
                    pass

        return device_names

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
                    routines = self.nucore_interface.get_routine_summary(r['routine_id'])
                    for routine in routines:
                        out.append(routine)
                except Exception as ex:
                    pass
        return out
        
