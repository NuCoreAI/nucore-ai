from __future__ import annotations

from typing import Any

from intent_handler import BaseIntentHandler, IntentHandlerResult
from rag import RAGData


class GroupSceneOperationsIntentHandler(BaseIntentHandler):
    """Intent handler for group and scene activation/management operations.

    Injects filtered device context from upstream dependency handlers (e.g.
    ``device_filter``) into the ``<<runtime_device_structure>>`` prompt
    placeholder, then asks the LLM to produce the appropriate group/scene
    commands.  The LLM response is returned as-is (plain text or tool calls)
    without any post-processing.
    """

    async def get_prompt_runtime_replacements(
        self,
        query,
        *,
        dependency_outputs: IntentHandlerResult | None = None,
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
            dependency_outputs:  Dict of ``intent_name → IntentHandlerResult``
                                 from preceding intents in the execution chain.
            framework_context:   Unused; present for interface compatibility.
            route_result:        Unused; present for interface compatibility.

        Returns:
            Dict mapping ``"<<runtime_device_structure>>"`` to the assembled
            device context string.
        """
        dout = ""
        if isinstance(dependency_outputs, dict):
            for dependency_output in dependency_outputs.values():
                if isinstance(dependency_output, IntentHandlerResult):
                    output = dependency_output.output
                    if isinstance(output, str):
                        dout += output + "\n\n"
                    elif isinstance(output, RAGData):
                        # Flatten RAG document list into plain text.
                        for document in output['documents']:
                            dout += document + "\n\n"

        return {"<<runtime_device_structure>>": dout}

    async def handle(
        self,
        query,
        *,
        route_result=None,
        framework_context: str = None,
        dependency_outputs: IntentHandlerResult | str | dict[str, Any] | None = None,
    ):
        """Call the LLM with device context and return its group/scene response.

        Args:
            query:               The user query string.
            route_result:        Routing metadata forwarded to message assembly
                                 and stamped on the result.
            framework_context:   Optional extra context string.
            dependency_outputs:  Outputs from preceding intents; passed through
                                 to :meth:`build_messages` for prompt injection.

        Returns:
            :class:`~intent_handler.IntentHandlerResult` with the LLM response
            and route metadata attached.
        """
        messages = await self.build_messages(
            query,
            dependency_outputs=dependency_outputs,
            framework_context=framework_context,
            route_result=route_result,
        )
        response = await self.call_llm(messages=messages)
        response.set_route_result(route_result=route_result)
        return response

