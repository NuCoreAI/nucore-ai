# Assistant session management
from typing import List, Dict


#This class manages long lasting session informaiton 
#specifically relating to mapping clairfying quesitons for devices and events
#It stores the history of interactions and relevant context to maintain continuity in conversations.
class AssistantSession:
    def __init__(self):
        self.messages: List[Dict[str, str]] = []
        self.redo_query=False
        self.last_user_query=""

    async def process_clarify_device_tool_call(self, clarify:dict, user_response: str) -> str:
        if not clarify or 'question' not in clarify or 'options' not in clarify:
            raise ValueError("Invalid clarify data")
        
        question = clarify['question']
        options = clarify['options']
        if not user_response:
            # ask the user for response
            user_response = input("\n\n ?  ").strip()
            if not user_response:
                self.redo_query = False
                raise ValueError("No user response provided")

        # find the option that includes at least one word from user response
        for option in options:
            option_name = option['name']
            if any(word.lower() in option_name.lower() for word in user_response.split()):
                selected_device = option_name
                break
            else:
                self.redo_query = False
                raise ValueError("No matching device found for user response")     
        umsg={
            "role":"user",
            "content":f"{self.last_user_query}" 
        }
        self.messages.append(umsg)
        amsg={
            "role":"assistant",
            "content":f"{question}" 
        }
        self.messages.append(amsg)
        umsg={
            "role":"user",
            "content":f"{selected_device}" 
        }
        self.messages.append(umsg)
        self.redo_query = True
        return selected_device

    async def process_clarify_event_tool_call(self, clarify:dict, user_response: str) -> str:
        if not clarify or 'question' not in clarify or 'options' not in clarify:
            raise ValueError("Invalid clarify data")
        
        question = clarify['question']
        options = clarify['options']
        if not user_response:
            # ask the user for response
            user_response = input("\n\n?").strip()
            if not user_response:
                self.redo_query = False
                raise ValueError("No user response provided")

        # find the option that includes at least one word from user response
        for option in options:
            option_name = option['name']
            if any(word.lower() in option_name.lower() for word in user_response.split()):
                selected_event = option_name
                break
            else:
                self.redo_query = False
                raise ValueError("No matching event found for user response")     
            
        amsg={
            "role":"assistant",
            "content":f"{question}" 
        }
        self.messages.append(amsg)
        umsg={
            "role":"user",
            "content":f"{selected_event}" 
        }
        self.messages.append(umsg)
        return selected_event

    def get_context(self) -> str:
        if not self.has_messages():
            return None
        output="Context:\n\n"
        for msg in self.messages:
            output+=f"{msg['role']}: {msg['content']}\n\n"

        return output.strip() 
    
    def has_messages(self) -> bool:
        return len(self.messages) > 0