from __future__ import annotations

from typing import Any, Literal

from intent_handler import BaseIntentHandler, IntentHandlerResult
from utils import get_logger


logger = get_logger(__name__)



class NodeOpsIntentHandler(BaseIntentHandler):
    """Intent handler for operations on nodes (devices, groups, folders).

    Uses route-provided context and runtime prompt data to inject node details
    into prompt placeholders so the LLM knows which nodes were matched.

    The LLM returns a list of ``{id, operation}`` dicts.  Each entry is
    dispatched to :meth:`~nucore.NuCoreInterface.node_ops` on the NuCore
    backend.  The collected results become ``response.output``.
    """

    async def get_prompt_runtime_replacements(
        self,
        query,
        *,
        framework_context=None,
        route_result=None,
    ) -> dict[str, str]:
        """Assemble the ``<<runtime_device_structure>>`` placeholder from dependency outputs.

        Concatenates text or RAG document content from all upstream handler
        results so the LLM has the relevant device context when generating
        group/scene commands.

        Args:
            query:               The user query (unused; reserved for subclass
                                 overrides).
            framework_context:   Unused; present for interface compatibility.
            route_result:        Unused; present for interface compatibility.

        Returns:
            Dict mapping ``"<<runtime_device_structure>>"`` to the assembled
            device context string.
        """

        if route_result:
            candidate_devices = self.get_route_context_value(route_result, "candidate_devices", [])
            candidate_rags = self._get_rags_from_candidates(candidate_devices, dedupe=False)  # RAG content is already de-duplicated at the source, and further deduping can remove important context.
            return {"<<runtime_device_structure>>": "" if not candidate_rags else candidate_rags}

        return {
            "<<runtime_device_structure>>": "No routine runtime information available."
        }

    async def handle(
        self,
        query,
        *,
        route_result=None,
        framework_context: dict = None,
        raw_response: IntentHandlerResult | None = None,
        tool_calls=None,
    ):
        """Execute node operations based on LLM tool calls and return results. 

        Args:
            query:               The user query string.
            route_result:        Routing metadata stamped on the result.
            framework_context:   Optional runtime context dictionary from eisyui showing which page/url we are on.

        Returns:
            :class:`~intent_handler.IntentHandlerResult` whose ``output`` is a
            list of backend operation results, or a "no operations found" string.
        """
        response = raw_response

        tool_calls = tool_calls if tool_calls is not None else response.get_tool_calls()

        if tool_calls:
            for tool_call in tool_calls:
                # Each tool call's args is a list of {id, operation} dicts.
                operations = tool_call.args
                if not operations:
                    continue
                for tool in operations:
                    operation = tool.get("operation")
                    if operation == "add_group" or operation == "add_folder":
                        new_name = tool.get("new_name")
                        if not new_name:
                            logger.debug(f"Tool call for '{operation}' missing required 'new_name' field: {tool}")
                            continue
                        rc = await self.nucore_interface.add_node(node_name=new_name, type="group" if operation == "add_group" else "folder")
                        response.add_tool_result(rc if rc is not None else f"Operation '{operation}' on node '{new_name}' failed.")
                        continue

                    node_id = tool.get("node_id")
                    if node_id is None:
                        response.add_tool_result(f"Tool call for operation '{operation}' missing required 'node_id' field: {tool}")
                        continue

                    rc = None
                    if operation == "rename":
                        new_name = tool.get("new_name", None)
                        if not new_name:
                            response.add_tool_result(f"Tool call for 'rename' operation missing required 'new_name' field: {tool}")
                            continue

                        rc = await self.nucore_interface.node_ops(node_id=node_id, operation=operation, new_name=new_name)
                    elif operation == "move":
                        new_parent = tool.get("new_parent_id", None)
                        new_parent = new_parent[:new_parent.find("|")] if "|" in new_parent else new_parent  # Extract node_id if the format is "node_id|node_name"
                        if not new_parent:
                            response.add_tool_result(f"Tool call for 'move' operation missing required 'new_parent' field: {tool}")
                            continue
                        rc = await self.nucore_interface.node_ops(node_id=node_id, operation=operation, new_parent_id=new_parent)
                    else:
                        rc = await self.nucore_interface.node_ops(node_id=node_id, operation=operation)

                    response.add_tool_result(rc if rc is not None else f"Operation '{operation}' on node '{node_id}' failed.")
        else:
            logger.debug("No tool calls found in the response.")

        response.set_route_result(route_result=route_result)
        return response

