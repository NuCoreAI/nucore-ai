from __future__ import annotations

import time
from typing import Any

from intent_handler import BaseIntentHandler, IntentHandlerResult
from rag import RAGData


class GroupSceneOperationsIntentHandler(BaseIntentHandler):
    """Intent handler for group and scene activation/management operations.

    Injects filtered device context from routing candidates into the
    ``<<runtime_device_structure>>`` prompt placeholder, then asks the LLM
    to produce the appropriate group/scene commands. The LLM response is
    returned as-is (plain text or tool calls) without any post-processing.
    """

    async def get_prompt_runtime_replacements(
        self,
        query,
        *,
        framework_context=None,
        route_result=None,
    ) -> dict[str, str]:
        """Assemble the ``<<runtime_device_structure>>`` placeholder.

        Args:
            query:               The user query (unused; reserved for subclass
                                 overrides).
            framework_context:   Unused; present for interface compatibility.
            route_result:        Unused; present for interface compatibility.

        Returns:
            Dict mapping ``"<<runtime_device_structure>>"`` to the assembled
            device context string.
        """
        if route_result and route_result.route_context:
            # Pull latest candidate devices from accumulated multi-step contexts.
            candidate_devices = self.get_route_context_value(route_result, "candidate_devices", [])
            candidate_rags = self._get_rags_from_candidates(candidate_devices, dedupe=False)
            return {"<<runtime_device_structure>>": "" if not candidate_rags else candidate_rags}

        return {"<<runtime_device_structure>>": "Not available!"}

    async def handle(
        self,
        query,
        *,
        route_result=None,
        framework_context: str = None,
        raw_response: IntentHandlerResult | None = None,
        tool_calls=None,
    ):
        """Call the LLM with device context and return its group/scene response.

        Args:
            query:               The user query string.
            route_result:        Routing metadata forwarded to message assembly
                                 and stamped on the result.
            framework_context:   Optional extra context string.

        Returns:
            :class:`~intent_handler.IntentHandlerResult` with the LLM response
            and route metadata attached.
        """
        response = raw_response
        if response is None:
            return None

        tools = tool_calls if tool_calls is not None else response.get_tool_calls()
        if tools:
            io = self.nucore_interface
            if io is None:
                return "NuCore interface is not available."
            for tool in tools:
                if tool.name == "tool_group_scene_api":
                    for step in self._normalize_steps(tool.args):
                        response.add_tool_result(tool_result=await self._execute_group_scene_step(step))
                    continue

                if tool.name == "tool_group_scene_multi_device_scene":
                    response.add_tool_result(tool_result=await self._execute_multi_device_scene(tool.args))
                    continue

                if tool.name not in {"tool_group_scene_api", "tool_group_scene_multi_device_scene"}:
                    response.add_tool_result(tool_result=f"Unknown tool called: {tool.name}")
                    continue

        response.set_route_result(route_result=route_result)
        return response

    def _normalize_steps(self, args: Any) -> list[dict[str, Any]]:
        if isinstance(args, list):
            return [item for item in args if isinstance(item, dict)]
        if isinstance(args, dict):
            steps = args.get("steps")
            if isinstance(steps, list):
                return [item for item in steps if isinstance(item, dict)]
            return [args]
        return []

    async def _execute_group_scene_step(self, step: dict[str, Any]) -> dict[str, Any] | str:

        operation = str(step.get("operation", "")).strip()
        args ={
            "controller_address": step.get("controller_address", None),
            "link_address": step.get("link_address", None),
            "is_controller": step.get("is_controller", False),
            "link": step.get("link", None),
            "name": step.get("name", None)
        }

        precheck = self._run_playbook_prechecks(operation=operation, args=args)
        if precheck.get("ok") is False:
            return {
                "successful": False,
                "stage": "precheck",
                "operation": operation,
                "error": precheck.get("error", "precheck failed"),
                "details": precheck,
            }

        io = self.nucore_interface
        try:
            if operation == "group_scene_add_member":
                return io.group_scene_add_member(
                    group_address=str(args.get("controller_address", "")).strip(),
                    link_address=str(args.get("link_address", "")).strip(),
                    is_controller=bool(args.get("is_controller", False)),
                    name=args.get("name", None) 
                )
            if operation == "group_scene_remove_member":
                return io.group_scene_remove_member(
                    group_address=str(args.get("controller_address", "")).strip(),
                    link_address=str(args.get("link_address", "")).strip(),
                )
            if operation == "group_scene_update_link":
                link = args.get("link") if isinstance(args.get("link"), dict) else {}
                link["node"] = str(args.get("link_address", "")).strip()
                return io.group_scene_update_link(
                    controller_address=str(args.get("controller_address", "")).strip(),
                    link_address=str(args.get("link_address", "")).strip(),
                    link=link,
                )
        except Exception as exc:
            return {"successful": False, "operation": operation, "error": f"request failed: {exc}"}

        return f"Invalid operation '{operation}'."

    def _run_playbook_prechecks(self, *, operation: str, args: dict[str, Any]) -> dict[str, Any]:

        if operation == "group_scene_add_member":
            link_address = str(args.get("link_address", "")).strip()
            controller_address = str(args.get("controller_address", "")).strip()
            is_controller = bool(args.get("is_controller", False))
            if not controller_address:
                return {"ok": False, "error": "controller_address is required"}
            if not link_address:
                return {"ok": False, "error": "link_address is required"}

            payload = self.nucore_interface.group_scene_get_node_roles(node_address=link_address)
            if payload is None:
                return {"ok": False, "error": "failed to fetch node roles"}

            try:
                data = payload.get("data", {}) if isinstance(payload, dict) else {}
                if is_controller and not bool(data.get("availableAsController", False)):
                    return {"ok": False, "error": "node is not available as controller", "nodeAddress": node_address}
                if not is_controller and not bool(data.get("availableAsResponder", False)):
                    return {"ok": False, "error": "node is not available as responder", "nodeAddress": node_address}
            except Exception as ex:
                return {"ok": False, "error": f"invalid nodeRoles response: {ex}"}

        if operation == "group_scene_remove_member":
            node_address = str(args.get("link_address", "")).strip()
            controller_address = str(args.get("controller_address", "")).strip()
            if not controller_address:
                return {"ok": False, "error": "controller_address is required"}
            if not node_address:
                return {"ok": False, "error": "link_address is required"}

        if operation == "group_scene_update_link":
            controller_address = str(args.get("controller_address", "")).strip()
            link = args.get("link") if isinstance(args.get("link"), dict) else {}
            if not controller_address:
                return {"ok": False, "error": "controller_address is required"}
            responder_address = str(args.get("link_address", "")).strip()
            if not responder_address:
                return {"ok": False, "error": "link_address is required"}
            link_type = str(link.get("type", "")).strip() if isinstance(link, dict) else ""
            if controller_address and responder_address and link_type:
                payload = self.nucore_interface.group_scene_get_link_types(
                    controller_address=controller_address,
                    responder_address=responder_address,
                )
                if payload is None:
                    return {"ok": False, "error": "failed to fetch link types"}
                try:
                    data = payload.get("data", {}) if isinstance(payload, dict) else {}
                    link_types = data.get("linkTypes", []) if isinstance(data, dict) else []
                    allowed = {
                        str(item.get("type", "")).strip()
                        for item in link_types
                        if isinstance(item, dict)
                    }
                    if allowed and link_type not in allowed:
                        return {
                            "ok": False,
                            "error": f"link type '{link_type}' is not valid for this controller/responder pair",
                            "allowed": sorted(allowed),
                        }
                except Exception:
                    return {"ok": False, "error": "invalid linkTypes response"}

        return {"ok": True}

    async def _execute_multi_device_scene(self, args: Any) -> dict[str, Any] | str:
        if not isinstance(args, dict):
            return "Invalid arguments for tool_group_scene_multi_device_scene."
        devices = args.get("devices")
        if not isinstance(devices, list) or not devices:
            return {"ok": False, "error": "Argument 'devices' must be a non-empty list."}
        
        #now go through all devices and their roles and make sure that if a device's role is controller
        #then it is not already a controller in another group/scene.
        for idx, device in enumerate(devices):
            if not isinstance(device, dict):
                return {"ok": False, "error": f"Device entry at index {idx} is not a valid object."}
            link_address = str(device.get("link_address", None))
            role = str(device.get("role", "")).strip().lower()
            if not link_address or role not in {"controller", "responder"}:
                return {"ok": False, "index": idx, "error": "role must be controller or responder"}

            if role == "controller":
                groups = self.nucore_interface.get_groups_for_device(link_address, controller_only=True)
                if groups and len(groups) > 0:
                    out=f"Device at index {idx} with address '{link_address}' is already a controller in : "
                    for group in groups:
                        out+=f"\n- {group.name} [{group.address}]"
                    return {"ok": False, "error": out}

        group_address = args.get("group_address", None)
        if group_address is None:
            group_name = args.get("group_name", None)
            num_groups = len(self.nucore_interface.groups)
            #create random name for group/scene if not provided since group_address is not provided. 
            group_name = group_name if group_name else f"NuCore_Scene_{num_groups+1}"
            #now let's create the group/scene and get its address for subsequent member additions.
            response = await self.nucore_interface.add_node(node_name=group_name, type="group")
            if response.status_code != 200:
                return {"ok": False, "error": f"Failed to create group/scene with name '{group_name}'."}

            time.sleep(2) 
            await self.nucore_interface._refresh_device_structure()

            # now go through nucore.interface's groups (it's a dict) to find the one we just created and get its address. We have to do this because the add_node response does not return the new node's address, and we need the address for subsequent member additions.
            group_address = None
            for address, group in self.nucore_interface.groups.items():
                if group.name == group_name:
                    group_address = address
                    break
            if not group_address:
                return {"ok": False, "error": f"Failed to find the address of the newly created group/scene with name '{group_name}'."}

        #now go through each device and add it as member to the scene with the desired role and collect the results.
        results: list[dict[str, Any] | str] = []
        for idx, device in enumerate(devices):
            link_address = str(device.get("link_address", "")).strip()
            role = str(device.get("role", "")).strip().lower()
            step = {
                "operation": "group_scene_add_member",
                "controller_address": group_address,
                "link_address": link_address,
                "is_controller": role == "controller",
                "name": device.get("name"),
            }
            result = await self._execute_group_scene_step(step)
            if isinstance(result, dict):
                result["index"] = idx
                result["link_address"] = link_address
                result["role"] = role
            results.append(result)

        successful = sum(1 for r in results if isinstance(r, dict) and bool(r.get("successful", False)))
        return {
            "ok": successful == len(results),
            "operation": "group_scene_multi_device_scene",
            "controller_address": group_address,
            "group_name": group_name,
            "summary": {
                "total": len(results),
                "successful": successful,
                "failed": len(results) - successful,
            },
            "results": results,
        }


