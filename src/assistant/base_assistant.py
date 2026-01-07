# Abstract/Base Assistant class for NuCore AI Assistant abstracting out common functionality 

from abc import ABC, abstractmethod

import asyncio
import re,os
import json
import argparse
from typing import Tuple

from nucore import NuCore, PromptFormatTypes 
from iox import IoXWrapper
from importlib.resources import files
from config import AIConfig
from utils import JSONDuplicateDetector 

import threading
import queue
import time
from dataclasses import dataclass


def get_data_directory(parent:str, subdir:str) -> str:
    """
    Returns the path to a subdirectory within a parent directory.
    
    Args:
        parent (str): The parent directory.
        subdir (str): The subdirectory to access.
        
    Returns:
        str: The path to the specified subdirectory.
    """

    return str(files(parent).joinpath(subdir)) if subdir else str(files(parent))

def get_parser_args():
    '''
        initialize command line arguments that can be used across chatbot
        as well as assistant
    '''
    parser = argparse.ArgumentParser(
        description="Loader for NuCore Profile and Nodes XML files."
    )
    parser.add_argument(
        "--url",
        dest="url",
        type=str,
        required=False,
        help="The URL to fetch nodes and profiles from the nucore platform",
    )
    parser.add_argument(
        "--username",
        dest="username",
        type=str,
        required=False,
        help="The username to authenticate with the nucore platform",
    )
    parser.add_argument(
        "--password",
        dest="password",
        type=str,
        required=False,
        help="The password to authenticate with the nucore platform",
    )
    parser.add_argument(
        "--collection_path",
        dest="collection_path",
        type=str,
        required=False,
        help="The path to the embedding collection db. If not provided, defaults to ~/.nucore_db.",
    )
    parser.add_argument(
        "--model_url",
        dest="model_url",
        type=str,
        required=False,
        help="The URL of the remote model. If provided, this should be a valid URL that responds to OpenAI's API requests.",
    )
    parser.add_argument(
        "--model_auth_token",
        dest="model_auth_token",
        type=str,
        required=False,
        help="Optional authentication token for the remote model API (if required by the remote model) to be used in the Authorization header. You are responsible for refreshing the token if needed.",
    )
    parser.add_argument(
        "--embedder_url",
        dest="embedder_url",
        type=str,
        required=False,
        help="The URL of the embedder service. If provided, this should be a valid URL that responds to OpenAI's API requests."
    )
    parser.add_argument(
        "--reranker_url",
        dest="reranker_url",
        type=str,
        required=False,
        help="The URL of the reranker service. If provided, this should be a valid URL that responds to OpenAI's API requests."
    )
    parser.add_argument(
        "--prompt_type",
        dest="prompt_type",
        type=str,
        required=False,
        default="per-device",
        help="The type of prompt to use (e.g., 'per-device', 'shared-features', etc.)",
    )
    return parser.parse_args()

@dataclass
class LLMQueueElement:
    query:str
    num_rag_results:int
    rerank:bool
    websocket:any
    text_only:bool


class NuCoreBaseAssistant(ABC):
    def __init__(self, args):
        #prompts_path = os.path.join(os.getcwd(), "src", "prompts", "nucore.openai.prompt") 
        self.debug_mode = True
        self.message_history = []
        if not args:
            raise ValueError("Arguments are required to initialize NuCoreAssistant")
        self.prompt_type = args.prompt_type if args.prompt_type else PromptFormatTypes.DEVICE 
        self.config = AIConfig() 
        self.system_prompt = self._get_system_prompt()
        self.tool_prompt = self._get_tools_prompt()
        self.nuCore = NuCore(
            collection_path=args.collection_path if args.collection_path else os.path.join(os.path.expanduser("~"), ".nucore_db"),
            collection_name="nucore.assistant",
            nucore_api=IoXWrapper(
                base_url=args.url,
                username=args.username,
                password=args.password
            ),
            embedder_url=args.embedder_url if args.embedder_url else self.config.getEmbedderURL(),
            reranker_url=args.reranker_url if args.reranker_url else self.config.getRerankerURL(),
            formatter_type=self.prompt_type
        )
        if not self.nuCore:
            raise ValueError("Failed to initialize NuCore. Please check your configuration.")
        model_url = args.model_url if args.model_url else self.config.getModelURL()
        if not model_url:
            raise ValueError("Model URL is required to initialize NuCoreAssistant")
        self.__model_url__ = model_url
        self.__model_auth_token__ = args.model_auth_token if args.model_auth_token else None
        ## Queue processing setup 
        self.request_queue = queue.Queue()
        self.worker_thread = None
        self.running = False
        self.is_busy = False
        
        self.device_docs = None
        self._start_queue_processor()

        self.duplicate_detector = None 
        check_duplicates, time_window_seconds = self._check_for_duplicate_tool_call()
        if check_duplicates:
            self.duplicate_detector = JSONDuplicateDetector(time_window_seconds=time_window_seconds)
        self.device_docs_sent = False
        self.device_structure_changed = True
        
        print (self.__model_url__)
        asyncio.run(self._sub_init()) #for subclass specific initialization

        ## subscribe to get events from devices
        self.nuCore.subscribe_events(self._on_device_event, self._on_connect_callback, self._on_disconnect_callback)

    async def _refresh_device_structure(self) -> bool:
        """
        Refresh device structure if necessary.
        Check for changes in device structure and update internal state if changes are detected.
        :return: True if device structure has changed, False otherwise.
        """
        if not self.device_structure_changed:
            return False #already refreshed no need to check again
        self.device_structure_changed = False 

        device_docs = ""
        if not self.nuCore.load(include_profiles=True):
            raise ValueError("Failed to load devices from NuCore. Please check your configuration.")

        rag = self.nuCore.format_nodes()
        if not rag:
            raise ValueError(f"Warning: No RAG documents found for node {self.nuCore.url}. Skipping.")

        rag_docs = rag["documents"]
        if not rag_docs:
            raise ValueError(f"Warning: No documents found in RAG for node {self.nuCore.url}. Skipping.")

        for rag_doc in rag_docs:
            device_docs += "\n" + rag_doc

#        if self.device_docs is None:
#            self.device_docs = device_docs
        self.device_docs = device_docs
        return True #changed

    async def _on_device_event(self, message:dict):
        """
        Callback function to handle device events.
        What we are looking for are events that change device structure such as device added/removed, property added/removed, etc.
        :param event: The event data received.
        """
        if message is None or 'node' not in message or 'control' not in message:
            print(f"Received invalid message format {message}")
            return
        
        control = message['control']
        if control == "_3": #node updated event
            self.device_structure_changed = True # just to be on the safe side

    async def _on_connect_callback(self):
        """
        Callback function to handle connection established event.
        """
        self.device_structure_changed = True # just to be on the safe side

    async def _on_disconnect_callback(self):
        """
        Callback function to handle disconnection event.
        """
        pass

    def _start_queue_processor(self):
        """Start the background worker thread"""
        if not self.running:
            self.running = True
            self.worker_thread = threading.Thread(target=self._process_queue, daemon=True)
            self.worker_thread.start()

    def _stop_queue_processor(self):
        """Stop the worker thread"""
        self.running = False
        self.request_queue.put(None)  # Signal to exit
        if self.worker_thread:
            self.worker_thread.join()

    @abstractmethod
    def _get_system_prompt(self) -> str:
        """
        Load the system prompt from the prompts file.
        :return: The system prompt as a string.
        """
        pass

    @abstractmethod
    async def _sub_init(self):
        """
        Subclass specific initialization.
        """
        pass

    async def __check_debug_mode__(self, query, websocket):
        """
        Check if the query is a debug command and process it accordingly.
        :param query: The customer input to check.
        :return: True if a debug command was processed, False otherwise.
        """
        debug_commands = [
            "/set_debug_on/",
            "/set_debug_off/"
        ]
        for command in debug_commands:
            if query.startswith(command):
                if command == "/set_debug_on/":
                    self.debug_mode = True
                    await self.send_response("Debug mode enabled.", True, websocket)
                    return True
                elif command == "/set_debug_off/":
                    self.debug_mode = False
                    await self.send_response("Debug mode disabled.", True, websocket)
                    return True
        
        return False    

    def set_remote_model_access_token(self, token: str):
        """
        You are responsible for refreshing the access token
        Set the remote model access token.
        :param token: The access token to set.
        """
        self.__model_auth_token__ = token


    async def create_automation_routines(self,routines:list, websocket):
        """
        Create automation routines in NuCore.
        :param routines: A list of routines to create.
        :return: The result of the routine creation.
        **for now, just a stub **
        """
        if not routines or len(routines) == 0:
            return "No routines provided"
        responses = []
        for routine in routines:
            responses.append(await self.nuCore.create_automation_routine(routine))
        await self.process_tool_responses(responses, websocket, "routine(s)")

    async def process_property_query(self, prop_query:list, websocket):
        if not prop_query or len(prop_query) == 0:
            return "No property query provided"
        try:
            if isinstance(prop_query[0], list): 
                prop_query = prop_query[0]
        except Exception as e:
            pass
        texts = [] 
        for property in prop_query:
            # Process the property query
            device_id = property.get('device') or property.get('device_id')
            if not device_id:
                print(f"No device ID provided for property query: {property}")
                continue
            properties = await self.nuCore.get_properties(device_id)
            if not properties:
                print(f"No properties found for device {device_id}")
                continue
            prop_id = property.get('property') or property.get('property_id')
            device_name = self.nuCore.get_device_name(device_id)
            if not device_name:
                device_name = device_id
            if prop_id:
                prop = properties.get(prop_id)
                if prop:
                    texts.append(f"{device_name}: {prop.formatted if prop.formatted else prop.value}")
                else:
                    texts.append(f"Property {prop_id} not found for device {device_name}")
                    print( f"Property {prop_id} not found for device {device_name}")
            else:
                print(f"No property ID provided for device {device_name}")

        if texts:
            await self.process_tool_responses(texts, websocket, "property query(ies)") 
            return  

    async def process_tool_responses(self, responses, websocket, type:str):
        if not responses or len(responses) == 0:
            await self.send_response("Assistant didn't return anything", True, websocket)
            return None
        if type is None:
            type="request(s)"
        output=f"rephrase_tool_results: results of the {len(responses)} {type}:\""
        if isinstance(responses, list):
            for i in range(len(responses)):
                response = responses[i]
                if isinstance(response, str):
                    output += f"\n{i+1} -> {response}"
                    continue
            #    original_message = original_messages[i] if original_messages and i < len(original_messages) else " "
                output += f"\n{i+1} -> {'successful' if response.status_code == 200 else 'failed with status code ' + str(response.status_code)}"
        else:
            output+=f"{'successful' if response.status_code == 200 else 'failed with status code ' + str(response.status_code)}"

        output+="\""
        await self.process_customer_input(output, websocket=websocket, text_only=True)
        return responses

    async def send_commands(self, commands:list, websocket):
        responses = await self.nuCore.send_commands(commands)
        return await self.process_tool_responses(responses, websocket, "command(s)")
    
    async def process_json_tool_call(self, tool_call:dict, websocket):
        if not tool_call:
            return None
        try:
            type = tool_call.get("tool")
            if not type:
                return None
            elif type == "PropsQuery":
                return await self.process_property_query(tool_call.get("args"), websocket)
            elif type == "Commands":
                return await self.send_commands(tool_call.get("args"), websocket)
            elif type == "Routines":
                return await self.create_automation_routines(tool_call.get("args"), websocket)

        except Exception as e:
            print(f"Error processing tool call: {e} {tool_call}")
            
        return None

    async def process_json_tool_calls(self, tool_calls, websocket):
        if isinstance(tool_calls, dict):
            return await self.process_json_tool_call(tool_calls, websocket)
        elif isinstance(tool_calls, list):
            for tool_call in tool_calls:
                return await self.process_json_tool_call(tool_call, websocket)
        return None

    async def process_tool_call(self,full_response:str, websocket, begin_marker, end_marker):
        if not full_response: 
            return None

        tools = None
        try:
            #remove markdowns such as ```json ... ```
            full_response = re.sub(r"```json(.*?)```", r"\1", full_response, flags=re.DOTALL).strip()
            if self.duplicate_detector is None:
                tools = json.loads(full_response)
                return await self.process_json_tool_calls(tools, websocket)

            tools = self.duplicate_detector.get_valid_json_objects(full_response, debug_mode=self.debug_mode)
            for tool in tools:
                await self.process_json_tool_calls(tool, websocket)
            #tools = json.loads(full_response)
            #return await self.process_json_tool_calls(tools, websocket)
        except Exception as ex:
            if not full_response:
                return ValueError("Invalid input to process_tool_call")
            else:
                print(f"Error parsing tool call JSON: {ex}")
                return None
            
    async def send_response(self, message, is_end=False, websocket=None):
        if not message:
            return None
        if websocket:
            payload={
                "sender": "bot",
                "message": message,
                "end": "true" if is_end else "false"
            }
            await websocket.send_text(json.dumps(payload))
        print(message, end="", flush=True)
        return message
    

    async def process_customer_input(self, query:str, num_rag_results=5, rerank=True, websocket=None, text_only:bool=False):
        """
        Submits to the queued worker to process the customer input using the underlying model with conversation state. 
        :param query: The customer input to process.
        :param num_rag_results: The number of RAG results to use for the actual query
        :param rerank: Whether to rerank the results.
        :param websocket: The websocket to send responses to (if any).
        :param text_only: Whether to return text only without processing tool calls
        """

        if not query:
            print("No query provided, exiting ...")
            return None
        query_element= LLMQueueElement(
            query=query,
            num_rag_results=num_rag_results,
            rerank=rerank,
            websocket=websocket,
            text_only=text_only
        )
        self.request_queue.put(query_element)

    def _process_queue(self):
        """Worker thread that processes requests one at a time"""
        while self.running:
            try:
                # Get next request (blocks until available)
                item = self.request_queue.get()

                if item is None:  # Stop signal
                    break

                # Process the LLM request
                import asyncio
                self.is_busy = True
                asyncio.run(self._process_customer_queue_element(item))
                self.is_busy = False

                self.request_queue.task_done()
            except queue.Empty:
                continue
        
    async def _process_customer_queue_element(self, element:LLMQueueElement):
        if element is None:
            return None

        query = element.query
        num_rag_results = element.num_rag_results
        rerank = element.rerank
        websocket = element.websocket
        text_only = element.text_only

        rc = await self.__check_debug_mode__(element.query, element.websocket)
        if rc:
            return None

        changed = await self._refresh_device_structure()
        if changed and len(self.message_history)>0:
            #reset message history if device docs have changed
            self.message_history = []

        query = query.strip()
        if not query:
            await self.send_response("No query provided, exiting ...", True, websocket)
            return None

        if query.startswith("?"):
            query = "\n"+query[1:].strip()
        
        sprompt = self.system_prompt.strip()
        user_content = f"USER QUERY:{query}"
        if changed or not self.device_docs_sent:        
            user_content = f"────────────────────────────────\n\n# DEVICE STRUCTURE\n\n{self.device_docs}\n\n{user_content}"
            self.device_docs_sent = True
            #user_content = f"\n\n# DEVICE STRUCTURE\n\n{device_docs}\n\n{user_content}"
        if len(self.message_history) == 0 :
            sprompt += "\n\n"+self.tool_prompt.strip()+"\n\n" #+self.nuCore.nucore_api.get_shared_enums().get_all_enum_sections().strip()
            if self._include_system_prompt_in_history():
                self.message_history.append({"role": "system", "content": sprompt})
            if self.debug_mode:
                with open("/tmp/nucore.1.prompt.txt", "w") as f:
                    f.write(sprompt)
                with open("/tmp/nucore.1.prompt.txt", "a") as f:
                    f.write(user_content)
        # Add user message to history
        self.message_history.append({"role": "user", "content": user_content})
        if self.debug_mode:
            with open("/tmp/nucore.prompt.txt", "w") as f:
                f.write(sprompt)
            with open("/tmp/nucore.prompt.txt", "a") as f:
                f.write(user_content)
        try:
            assistant_response = await self._process_customer_input(num_rag_results=num_rag_results, rerank=rerank, websocket=websocket, text_only=text_only)
            if assistant_response is not None:
                self.message_history.append({"role": "assistant", "content": assistant_response})
        except Exception as e:
            print(f"An error occurred while processing the customer input: {e}")
            import traceback
            traceback.print_exc()
            return None

    @abstractmethod
    def _include_system_prompt_in_history(self) -> bool:
        """
        Whether to include the system prompt in the message history.
        :return: True if the system prompt should be included, False otherwise.
        """
        return True
    
    @abstractmethod
    def _get_tools_prompt(self) -> str:
        """
        Returns the tools prompt to be included in the system prompt.
        This is for models that do not support tool calls natively.
        :return: The tools prompt as a string.
        """
        return "" 

    @abstractmethod
    async def _process_customer_input(self, num_rag_results:int, rerank:bool, websocket, text_only:bool)-> str:
        """
        :param num_rag_results: The number of RAG results to use for the actual query
        :param rerank: Whether to rerank the results.
        :param websocket: The websocket to send responses to (if any).
        :param text_only: Whether to return text only without processing tool calls
        Process the customer input using the underlying model with conversation state.
        :return: The full response as a string.
        """
        return None 
    
    @abstractmethod
    def _check_for_duplicate_tool_call(self) -> Tuple[bool, int]:
        """
            Abstract method to check for duplicate tool calls. 
            :return: A tuple (is_duplicate: bool, time_window_seconds: int)
        """
        return False, 0 


    async def main(self, welcome_message:str=None):
        if welcome_message:
            print(welcome_message)
        else:
            print("Welcome to NuCore AI Assistant!")
    
        print("Type 'quit' to exit")
        i=0
        
        while True:
            try:

                while self.is_busy or not self.request_queue.empty() :
                    time.sleep(1)

                user_input = input("\nWhat can I do for you? > " if i==0 else "\n> ").strip()
                i+=1

                if not user_input:
                    print("Please enter a valid request")
                    continue

                if user_input.lower() == 'quit':
                    print("Goodbye!")
                    break
                await self.process_customer_input(user_input, num_rag_results=3, rerank=False)
                
            except Exception as e:
                print(f"An error occurred: {e}")
                continue
            
