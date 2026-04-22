from __future__ import annotations
from typing import Any

from intent_handler import BaseIntentHandler
from time import sleep
from intent_handler.base import IntentHandlerResult
from rag import DedupeDevices

import logging


from nucore import Node
from nucore import NuCoreError
from rag import RAGData, RAGFormatter, ProfileRagFormatter, MinimalRagFormatter


logger = logging.getLogger(__name__)

class PromptFormatTypes:
    DEVICE = "per-device"
    PROFILE = "shared-features"


def debug(msg):
    logger.debug(f"[PROFILE FORMAT ERROR] {msg}")

class DeviceFilterIntentHandler(BaseIntentHandler):

    def get_prompt_runtime_replacements(self, query, *, dependency_outputs:IntentHandlerResult| None = None, framework_context=None, route_result=None):
        self.nucore_interface._refresh_device_structure() # ensure we have the latest device structure before handling the intent   
        return {
            "<<device_database>>": self.nucore_interface.summary_rags.docs_to_string() if self.nucore_interface.summary_rags else ""
        }
    
    async def handle(self, query, *, route_result=None, framework_context:str=None, dependency_outputs:IntentHandlerResult | str | dict[str, Any] | None = None):
        messages = self.build_messages(
            query,
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
        
            rag_docs = self._get_rags_from_candidates(tools[0])
            if not rag_docs: #or len(rag_docs['documents']) == 0:
                debug("No matched devices found in the RAG data.")
                response.set_route_result(route_result=route_result)
                return response

            response.output = rag_docs
            response.set_route_result(route_result=route_result)
            return response

        debug("Invalid response.")
        response.set_route_result(route_result=route_result)
        return response

    def _get_rags_from_candidates(self, tool: dict) -> RAGData:
        """
        Get RAGData for the matched devices in the intent.
        
        :param tool_call: Dictionary representing the tool call from the LLM response
        :return: RAGData object containing only the matched devices
        """
        full_rags = self.nucore_interface.rags
        if not full_rags:
            return RAGData()

        score_threshold = self.config.get("threshold", 0.80)

        matched_candidate_ids=set()
        devices = tool.args
        for d in devices:
            if float(d.get('score', 0)) >= score_threshold:
                try:
                    matched_candidate_ids.add(d['device_id'])
                except Exception as ex:
                    pass
        
        filtered_rags = RAGData(documents=[], ids=[]) 

        for idx, id_ in enumerate(full_rags["ids"]):
            if id_ in matched_candidate_ids:
                filtered_rags.add_document(full_rags["documents"][idx], full_rags["embeddings"][idx] , id_, full_rags["metadatas"][idx])

        rag_docs = filtered_rags["documents"]
        if not rag_docs:
            return "" 
        device_docs = ""
        for rag_doc in rag_docs:
            device_docs += "\n" + rag_doc
        
        deduper = DedupeDevices()
        deduped_docs = deduper.dedupe(device_docs)
        return deduped_docs 

