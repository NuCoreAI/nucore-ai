"""
NuCore Prompt  ... encapsulates prompt and tool management for NuCore agents.
"""

import re
from typing import List
from dataclasses import dataclass, field
from rag import RAGData

DEFAULT_MAX_CONTEXT_SIZE = 32000
DEFAULT_TOKENS_PER_MESSAGE = 2600

ROUTER_INTENT = '__router__'  # Special intent name for router
ROUTER_DEVICE_SECTION = '''
────────────────────────────────
# DEVICE DATABASE

'''
AGENT_DEVICE_SECTION = '''
────────────────────────────────
# DEVICE STRUCTURE

'''

USER_QUERY_SECTION = '''
────────────────────────────────
# USER QUERY 

'''

@dataclass
class NuCorePrompt:
    """
    Encapsulates a complete NuCore agent prompt with all context.
    
    Attributes:
        prompt: The fully resolved agent prompt string
        tools: List of tool schemas for this intent
        intent: The intent name (e.g., 'command_control', 'routine_automation')
        keywords: List of extracted keyword dictionaries from router
        devices: List of matched device dictionaries with scores from router
        message_history: Optional list of prior messages for context
        max_context_size: Maximum token context size for the model
        tokens_per_message: Estimated tokens used per message in conversation
    """
    prompt: str = None
    tools: List[dict] =  None
    intent: str = None
    keywords: List[dict] = None 
    rags: RAGData = None 
    message_history: List[dict] = field(default_factory=list)
    max_context_size: int = DEFAULT_MAX_CONTEXT_SIZE
    tokens_per_message: int = DEFAULT_TOKENS_PER_MESSAGE

    @staticmethod
    def get_user_query_section(user_query:str)-> str:
        """
        Get the formatted user query section.
        
        :param user_query: The user's query string
        :return: Formatted user query section string
        """
        return f"{USER_QUERY_SECTION}{user_query}\n"

    def is_router(self) -> bool:
        """
        Check if this prompt is for the router intent.
        
        :return: True if router intent, False otherwise
        """
        return self.intent == ROUTER_INTENT

    def add_history(self, history:dict):
        """
        Append a message to the message history.
        
        """
        self.message_history.append(history)
    
    def clear_history(self):
        """
        Clear the message history.
        """
        self.message_history = []

    def search_history(self, role:str, content_substr:str)->bool:
        """
        Search message history for a message with given role and content substring.
        return True if exists otherwise False.
        
        :param role: Role to search for (e.g., 'user', 'assistant')
        :param content_substr: Substring to search within message content
        :return: True if found, False otherwise
        """
        for msg in self.message_history:
            if msg["role"] == role and content_substr in msg["content"]: 
                    return True
        return False    

    def set_device_rags(self, rags:RAGData):
        if self.is_router():
            new_device_docs = self._get_device_docs(rags)
            if self.rags:
                existing_device_docs = self._get_device_docs(self.rags)
                if new_device_docs != existing_device_docs:
                    self.clear_history()
        self.rags = rags

    def get_device_docs(self)->str:
        """
        Searches for the device id (rag[id]) in message_history for items where the role is 'user'. 
        If found, that document is ignored, otherwise it's added to the list of documents to be returned. 
        :return: Concatenated string of device documents that have not been sent yet. 
        :rtype: str
        """
        if self.rags == None:
            return ""
        if self.is_router():
            return self._get_device_docs(self.rags)

        not_sent_rags=RAGData(documents=[], ids=[])
        for idx, id_ in enumerate(self.rags["ids"]):
            device_id=f"\"id\":\"{id_}\""
            if self.search_history("user", device_id):
                continue
            not_sent_rags.add_document(self.rags["documents"][idx], self.rags["embeddings"][idx] , id_, self.rags["metadatas"][idx])

        return self._get_device_docs(not_sent_rags)
    

    def _get_device_docs(self, rags:RAGData)->str:
        if rags == None:
            return "" 

        rag_docs = rags["documents"]
        if not rag_docs:
            return "" 
        device_docs = ROUTER_DEVICE_SECTION if self.is_router() else AGENT_DEVICE_SECTION 
        for rag_doc in rag_docs:
            device_docs += "\n" + rag_doc

        return device_docs

    def _estimate_tokens(self, text:str) -> int:
        """
        Estimate the number of tokens in a given text.
        :param text: The text to estimate tokens for.
        :return: Estimated number of tokens.
        """
        if not text:
            return 0
        # Simple estimation: 1 token per 4 characters (this is a rough estimate)
        return len(text) // 4 + 50  # adding buffer

    def trim_message_history(self):
        """
        Trim message_history to stay within context limits.
        Preserves: system message, first user message (with device structure), and recent conversation.
        """
        if len(self.message_history) <= 2:
            return
        
        # Calculate tokens used by system and first user message
        system_tokens = 0 
        first_user_tokens = 0

        idx = 0
        if idx < len(self.message_history) and self.message_history[idx]["role"] == "system":
            system_tokens = self._estimate_tokens(self.message_history[idx]["content"])
            idx += 1
        
        idx = 1
        if idx < len(self.message_history) and self.message_history[idx]["role"] == "user":
            first_user_tokens = self._estimate_tokens(self.message_history[idx]["content"])
        
        # Calculate available tokens for conversation history
        reserved_tokens = system_tokens + first_user_tokens + self.tokens_per_message
        available_tokens = self.max_context_size - reserved_tokens
        max_turns = max(1, available_tokens // self.tokens_per_message)
        
        # Get messages to keep
        system_msg = self.message_history[0] if self.message_history[0]["role"] == "system" else None
        first_user_msg = None
        conversation_msgs = []
        
        idx = 1 if system_msg else 0
        if idx < len(self.message_history) and self.message_history[idx]["role"] == "user":
            first_user_msg = self.message_history[idx]
            idx += 1
        
        conversation_msgs = self.message_history[idx:]
        
        # Keep only the most recent turns
        max_messages = max_turns * 2
        if len(conversation_msgs) > max_messages:
            conversation_msgs = conversation_msgs[-max_messages:]
        
        # Rebuild
        self.message_history = []
        if system_msg:
            self.message_history.append(system_msg)
        if first_user_msg:
            self.message_history.append(first_user_msg)
        self.message_history.extend(conversation_msgs)
        # if the last message is from "assistant", we must remove it otherwise LLM will get confused and thinks that it already responded.
        if self.message_history and self.message_history[-1]["role"] == "assistant":
            self.message_history = self.message_history[:-1]
        
