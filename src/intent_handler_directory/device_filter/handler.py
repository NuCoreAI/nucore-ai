from __future__ import annotations
from typing import Any

from intent_handler import BaseIntentHandler
from intent_handler.base import IntentHandlerResult
from rag import DedupeDevices, RAGData
from utils import get_logger


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Prompt format type constants
# ---------------------------------------------------------------------------

class PromptFormatTypes:
    """Named constants for the two prompt layout modes.

    * ``DEVICE``  — one prompt block per device (per-device detail mode).
    * ``PROFILE`` — a single shared-features block across devices.
    """

    DEVICE = "per-device"
    PROFILE = "shared-features"


def debug(msg: str) -> None:
    """Log a debug-level message prefixed with ``[PROFILE FORMAT ERROR]``."""
    logger.debug(f"[PROFILE FORMAT ERROR] {msg}")


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

class DeviceFilterIntentHandler(BaseIntentHandler):
    """Intent handler that filters the full device list down to a relevant subset.

    The LLM is given a compact device summary (``<<device_database>>``) and
    returns a ``tool_device_filter`` tool call containing candidate device IDs
    scored by relevance.  Only candidates at or above the configured
    ``threshold`` (default ``0.80``) are kept.

    The handler output is a :class:`~rag.RAGData`-compatible string of
    de-duplicated device documents, which downstream handlers (e.g.
    ``command_control_status``) can consume as dependency output.
    """

    async def get_prompt_runtime_replacements(
        self,
        query,
        *,
        dependency_outputs: IntentHandlerResult | None = None,
        framework_context=None,
        route_result=None,
    ) -> dict[str, str]:
        """Refresh the device structure and supply the ``<<device_database>>`` placeholder.

        Forces a refresh of the NuCore device structure before every call so
        the LLM always sees the current device list rather than a stale cache.

        Args:
            query:              The user query (unused; reserved for subclass
                                overrides).
            dependency_outputs: Unused for this handler.
            framework_context:  Unused for this handler.
            route_result:       Unused for this handler.

        Returns:
            Dict with the single key ``"<<device_database>>"`` mapped to the
            full device summary string, or an empty string when no summary RAGs
            are available.
        """
        # Ensure we have the latest device structure before building the prompt.
        await self.nucore_interface._refresh_device_structure()
        return {
            "<<device_database>>": (
                self.nucore_interface.summary_rags.docs_to_string()
                if self.nucore_interface.summary_rags
                else ""
            )
        }

    async def handle(
        self,
        query,
        *,
        route_result=None,
        framework_context: str = None,
        dependency_outputs: IntentHandlerResult | str | dict[str, Any] | None = None,
    ):
        """Call the LLM to identify relevant devices and return their RAG documents.

        Pipeline:
        1. Build the message list (prompt + device database).
        2. Call the LLM expecting a JSON/tool response.
        3. Extract tool calls; if none, return the response as-is.
        4. Convert the first tool call's candidate list into filtered RAG docs.
        5. Attach the filtered docs as the response output.

        Args:
            query:               The user query string.
            route_result:        Routing metadata forwarded to message assembly.
            framework_context:   Optional extra context string.
            dependency_outputs:  Unused for this handler (no upstream deps).

        Returns:
            :class:`~intent_handler.IntentHandlerResult` whose ``output`` is
            the de-duplicated device document string, or the bare LLM response
            when no matching devices are found.
        """
        messages = await self.build_messages(
            query,
            framework_context=framework_context,
            route_result=route_result,
        )
        response = await self.call_llm(messages=messages, expect_json=True)

        if isinstance(response, IntentHandlerResult):
            tools = response.get_tool_calls()
            if not tools:
                debug("No tool calls found in the response.")
                response.set_route_result(route_result=route_result)
                return response

            rag_docs = self._get_rags_from_candidates(tools[0])
            if not rag_docs:
                debug("No matched devices found in the RAG data.")
                response.set_route_result(route_result=route_result)
                return response

            # Replace the raw LLM output with the filtered device documents.
            response.output = rag_docs
            response.set_route_result(route_result=route_result)
            return response

        debug("Invalid response.")
        response.set_route_result(route_result=route_result)
        return response

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_rags_from_candidates(self, tool) -> str:
        """Filter the full RAG store to the devices nominated by the LLM tool call.

        Iterates the candidate list in ``tool.args``, discards entries below
        the configured score threshold, then extracts and de-duplicates the
        matching RAG documents.

        Args:
            tool: :class:`~intent_handler.adapters.ToolCall` whose ``args`` is
                  a list of dicts with ``device_id`` and ``score`` keys.

        Returns:
            De-duplicated device document string ready for downstream prompt
            injection, or an empty string when no candidates pass the threshold.
        """
        full_rags = self.nucore_interface.rags
        if not full_rags:
            return RAGData()

        # Threshold is configurable per-intent; fall back to 0.80.
        score_threshold = self.config.get("threshold", 0.80)

        # Collect device IDs that meet the relevance threshold.
        matched_candidate_ids: set[str] = set()
        devices = tool.args
        for d in devices:
            if float(d.get('score', 0)) >= score_threshold:
                try:
                    matched_candidate_ids.add(d['device_id'])
                except Exception:
                    pass

        # Build a filtered RAGData containing only the matched devices.
        filtered_rags = RAGData(documents=[], ids=[])
        for idx, id_ in enumerate(full_rags["ids"]):
            if id_ in matched_candidate_ids:
                filtered_rags.add_document(
                    full_rags["documents"][idx],
                    full_rags["embeddings"][idx],
                    id_,
                    full_rags["metadatas"][idx],
                )

        rag_docs = filtered_rags["documents"]
        if not rag_docs:
            return ""

        # Concatenate documents then de-duplicate overlapping content.
        device_docs = ""
        for rag_doc in rag_docs:
            device_docs += "\n" + rag_doc

        deduper = DedupeDevices()
        return deduper.dedupe(device_docs)


