"""
NuCore Prompt  ... encapsulates prompt and tool management for NuCore agents.
"""

import re
from typing import List
from dataclasses import dataclass, field
from rag import RAGData

DEFAULT_MAX_CONTEXT_SIZE = 32000
DEFAULT_TOKENS_PER_MESSAGE = 2600
DEFAULT_SCORE_THRESHOLD = 0.7

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

REPHRASE_INSTRUCTION = '''Now rephrase in brief NATURAL LANGUAGE. **NO JSON OUTPUT**. **NO DETAILS**\n'''


LOG_STARTED: bool = False

@dataclass
class NuCorePrompt:
    """
    Encapsulates a complete NuCore agent prompt with all context.
    
    Attributes:
        prompt: The fully resolved agent prompt string
        model: The model to use for this prompt
        tools: List of tool schemas for this intent
        intent: The intent name (e.g., 'command_control', 'routine_automation')
        keywords: List of extracted keyword dictionaries from router
        devices: List of matched device dictionaries with scores from router
        message_history: Optional list of prior messages for context
        max_context_size: Maximum token context size for the model
        tokens_per_message: Estimated tokens used per message in conversation
        score_threshold: Minimum score threshold for including device documents
    """
    prompt: str = None
    model: str = None
    tools: List[dict] =  None
    intent: str = None
    keywords: List[dict] = None 
    rags: RAGData = None 
    message_history: List[dict] = field(default_factory=list)
    max_context_size: int = DEFAULT_MAX_CONTEXT_SIZE
    tokens_per_message: int = DEFAULT_TOKENS_PER_MESSAGE
    score_threshold: float = DEFAULT_SCORE_THRESHOLD
    system_index : int = None # index of system message in history (if any)
    device_docs_index : int = None # index of device docs message in history (if any)

    def add_user_query_section(self, user_query:str, is_rephrase:bool=False):
        """
        add the formatted user query section. a
        includes device docs
        We must create two distinct messages so that we can preserve the device docs in the conversation history for future reference by the model. if we merge them into one message, then when the user query changes in the next turn, we lose that information from the conversation history.

        :param user_query: The user's query string
        :param is_rephrase: Flag indicating if this is a rephrase operation
        :return: Formatted user query section string

        """
        if not is_rephrase:
            device_docs = self.get_device_docs()
            if device_docs and not self.search_history("user", device_docs):
                if self.device_docs_index is not None:
                    self.clear_history()
                self.device_docs_index = self.add_history("user", device_docs)

        user_content = f"\n{USER_QUERY_SECTION}\n{user_query}"
        self.add_history("user", user_content + "\n") 

    def is_router(self) -> bool:
        """
        Check if this prompt is for the router intent.
        
        :return: True if router intent, False otherwise
        """
        return self.intent == ROUTER_INTENT

    def add_history(self, role:str, content:str):
        """
        Append a message to the message history.
        :param role: Role of the message (e.g., 'system', 'user', 'assistant')
        :param content: Content of the message
        :return the index of the added message in the history (useful for reference)
        """
        payload={"role": role, "content": content}
        self.trim_message_history(payload)
        self.message_history.append(payload)
        self._debug_output(payload)
        return len(self.message_history) - 1
    
    def add_system_message(self, role:str, content:str, force_add:bool=False):
        """
        Append a system message to the message history. checks for duplicates before adding.
        :param role: Role of the message (e.g., 'system')
        :param content: Content of the message
        :param force_add: If True, add the message even if it already exists in history
        """
        if not force_add and self.search_history(role, content):
            return
        if self.system_index is not None:
            #update existing system message
            self.message_history[self.system_index]["content"] = content
            self._debug_output(f"Updated system message at index {self.system_index}: \n {content}")
        else:
            self.system_index = self.add_history(role, content)

    def add_assistant_message(self, content:str):
        """
        Append an assistant message to the message history.
        :param content: Content of the assistant's message
        """
        if not content:
            return
        #remove all json code blocks from content before checking for duplicates in history, since assistant responses may include tool response sections that we don't want to consider for duplicate checking. we only want to check the natural language part of the response for duplicates.
        content_no_json = NuCorePrompt.strip_json(content)
        if len (content_no_json) > 10: # if the non-json content is very short, then we won't consider it for duplicate checking since it's likely not meaningful enough on its own. this is to avoid false positives in duplicate checking when the assistant response is mostly structured data with very little natural language content.
            self.add_history("assistant", content)

    def clear_history(self, include_system:bool=False):
        """
        Clear the message history.
        """
        if include_system:
            self.system_index = None
            self.message_history = []
        else:
            #preserve system message if exists
            sys_message = None
            if self.system_index is not None and self.system_index < len(self.message_history):
                sys_message = self.message_history[self.system_index]
            self.message_history = []
            if sys_message:
                self.system_index = 0
                self.message_history.append(sys_message)
            else:
                self.system_index = None
        self.device_docs_index = None

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
        return self._get_device_docs(self.rags)
        #if self.is_router():
        #    return self._get_device_docs(self.rags)

        #not_sent_rags=RAGData(documents=[], ids=[])
        #for idx, id_ in enumerate(self.rags["ids"]):
        #    device_id=f"\"id\":\"{id_}\""
        #    if self.search_history("user", device_id):
        #        continue
        #    not_sent_rags.add_document(self.rags["documents"][idx], self.rags["embeddings"][idx] , id_, self.rags["metadatas"][idx])

        #return self._get_device_docs(not_sent_rags)

    def _get_device_docs(self, rags:RAGData)->str:
        if rags == None:
            return "" 

        rag_docs = rags["documents"]
        if not rag_docs:
            return "" 
        header = ROUTER_DEVICE_SECTION if self.is_router() else AGENT_DEVICE_SECTION 
        device_docs = ""
        for rag_doc in rag_docs:
            device_docs += "\n" + rag_doc
        if self.is_router():
            return header + device_docs
        
        from rag import DedupeDevices
        deduper = DedupeDevices()
        deduped_docs = deduper.dedupe(device_docs)
        return header + deduped_docs

        #return device_docs

    def _estimate_tokens(self, message:dict) -> int:
        """
        Estimate the number of tokens in a given text.
        :param text: The text to estimate tokens for.
        :return: Estimated number of tokens.
        """
        if not message:
            return 0
        #convert dict to string
        text = str(message)
        # Simple estimation: 1 token per 4 characters (this is a rough estimate)
        return len(text) // 4 + 50  # adding buffer

    @staticmethod 
    def strip_json(text: str) -> str:
        """Remove all JSON objects ({...}) and arrays ([...]) from a string."""
        result = []
        depth = 0
        in_string = False
        escape = False
        for ch in text:
            if escape:
                escape = False
                if depth == 0:
                    result.append(ch)
                continue
            if ch == '\\' and in_string:
                escape = True
                if depth == 0:
                    result.append(ch)
                continue
            if ch == '"' and depth > 0:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in ('{', '['):
                depth += 1
                continue
            if ch in ('}', ']'):
                depth -= 1
                continue
            if depth == 0:
                result.append(ch)
        return ''.join(result)

    def trim_message_history(self, content_to_add:dict):
        """
        Trim message_history to stay within context limits.
        Preserves: system message, first user message (with device structure), and recent conversation.
        """
        if len(self.message_history) <= 2:
            return
        
        # Calculate tokens used by system and first user message
        system_tokens = 0 
        first_user_tokens = 0

        if self.system_index is not None and self.system_index < len(self.message_history):
            system_tokens = self._estimate_tokens(self.message_history[self.system_index])

        if self.device_docs_index is not None and self.device_docs_index < len(self.message_history):
            first_user_tokens = self._estimate_tokens(self.message_history[self.device_docs_index])

        #minimum tokens already used
        reserved_tokens = system_tokens + first_user_tokens 
        if reserved_tokens >= self.max_context_size:
            # Can't fit anything, keep only system and first user message
            self.message_history = self.message_history[:2]
            return

        new_content_tokens = self._estimate_tokens(content_to_add)
        reserved_tokens += new_content_tokens
        if reserved_tokens >= self.max_context_size:
            # Can't fit anything, keep only system and first user message
            self.message_history = self.message_history[:2]
            return
        #otherwise trim conversation history using this algorithm:
        available_tokens = self.max_context_size - reserved_tokens

        if available_tokens > reserved_tokens:
            return

        # Get messages to keep
        sys_messages = []
        idx=0
        if self.system_index is not None and self.system_index < len(self.message_history):
            sys_messages.append(self.message_history[self.system_index])
            self.system_index = idx
            idx+=1
        if self.device_docs_index is not None and self.device_docs_index < len(self.message_history):
            sys_messages.append(self.message_history[self.device_docs_index])
            self.device_docs_index = idx
            idx+=1
        
        history_messages = [] # in reversed order
        # Start from from the bottom to the 2nd element (ignoring system and first user)
        for idx, message in enumerate(reversed(self.message_history[2:]), start=2):
            token_count = self._estimate_tokens(message)
            available_tokens -= token_count 
            max_turns = max(1, available_tokens // self.tokens_per_message)
            if max_turns < 2:
                # ignore it
                available_tokens += token_count
                continue
            history_messages.append(message)
        
        # Rebuild
        self.message_history = []
        self.message_history.extend(sys_messages)
        conversation_msgs = list(reversed(history_messages))
        self.message_history.extend(conversation_msgs)
        
    def set_debug_mode(self, debug:bool):
        self.debug_mode = debug

    def _debug_output(self, message):
        if not self.debug_mode or not message:
            return
        global LOG_STARTED
        file_modifier="w" if not LOG_STARTED else "a"
        LOG_STARTED = True 
        
        with open("/tmp/nucore.prompt.md", file_modifier) as f:
            if isinstance(message, dict):
                f.write(f"\n\n################\nRole: *{message['role']}*\nIntent: *{self.intent}*\nMessage:\n\n")
                f.write(str(message["content"]))
            else:
                f.write(f"\n\n################\n{str(message)}\n\n")
