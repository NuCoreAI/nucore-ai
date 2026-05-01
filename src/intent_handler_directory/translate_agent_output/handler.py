from __future__ import annotations

from typing import Any

from intent_handler import BaseIntentHandler, IntentHandlerResult


class TranslateAgentOutputIntentHandler(BaseIntentHandler):
    """Intent handler that translates raw agent output into a user-friendly response.

    Acts as a post-processing step at the end of an execution chain.  The raw
    output from upstream handlers is passed through ``dependency_outputs`` and
    injected into the prompt by :meth:`build_messages`; the LLM then rewrites
    it into natural language suitable for the end user.

    This handler has no dynamic prompt placeholders —
    ``get_prompt_runtime_replacements`` returns an empty dict, relying entirely
    on the static prompt template and the dependency output injection performed
    by the base class.
    """

    async def get_prompt_runtime_replacements(
        self,
        query,
        *,
        dependency_outputs: IntentHandlerResult | None = None,
        framework_context=None,
        route_result=None,
    ) -> dict:
        """Return an empty replacement dict (no dynamic placeholders needed).

        Args:
            query:              The user query (unused).
            dependency_outputs: Unused for this handler.
            framework_context:  Unused for this handler.
            route_result:       Unused for this handler.

        Returns:
            Empty dict — the static prompt requires no runtime substitution.
        """
        return {}

    async def handle(
        self,
        query,
        *,
        route_result=None,
        framework_context: str | None = None,
        dependency_outputs: IntentHandlerResult | str | dict[str, Any] | None = None,
    ):
        """Translate upstream agent output into a natural-language response.

        Builds the message list (incorporating ``dependency_outputs`` as
        context), calls the LLM, and returns the rewritten response.

        Args:
            query:               The original user query string.
            route_result:        Routing metadata stamped on the result.
            framework_context:   Optional extra context string.
            dependency_outputs:  Raw output from preceding intents in the chain;
                                 injected into the prompt by :meth:`build_messages`
                                 so the LLM can translate it.

        Returns:
            :class:`~intent_handler.IntentHandlerResult` containing the
            translated, user-facing text response.
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