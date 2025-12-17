# Abstract/Base Assistant class for NuCore AI Assistant abstracting out common functionality 

from abc import ABC, abstractmethod

import re,os
import json
import asyncio, argparse

from nucore import NuCore 
from iox import IoXWrapper
from importlib.resources import files
from config import AIConfig

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
    return parser.parse_args()

class NuCoreBaseAssistant(ABC):
    def __init__(self, args):
        #prompts_path = os.path.join(os.getcwd(), "src", "prompts", "nucore.openai.prompt") 
        self.debug_mode = False
        self.message_history = []
        if not args:
            raise ValueError("Arguments are required to initialize NuCoreAssistant")
        self.config = AIConfig() 
        self.system_prompt = self._get_system_prompt()
        self.nuCore = NuCore(
            collection_path=args.collection_path if args.collection_path else os.path.join(os.path.expanduser("~"), ".nucore_db"),
            collection_name="nucore.assistant",
            nucore_api=IoXWrapper(
                base_url=args.url,
                username=args.username,
                password=args.password
            ),
            embedder_url=args.embedder_url if args.embedder_url else self.config.getEmbedderURL(),
            reranker_url=args.reranker_url if args.reranker_url else self.config.getRerankerURL()
        )
        if not self.nuCore:
            raise ValueError("Failed to initialize NuCore. Please check your configuration.")
        model_url = args.model_url if args.model_url else self.config.getModelURL()
        if not model_url:
            raise ValueError("Model URL is required to initialize NuCoreAssistant")
        self.__model_url__ = model_url
        self.__model_auth_token__ = args.model_auth_token if args.model_auth_token else None
        print (self.__model_url__)
        self.device_docs = None
        self.nuCore.load()
        self._sub_init() #for subclass specific initialization

    @abstractmethod
    def _get_system_prompt(self) -> str:
        """
        Load the system prompt from the prompts file.
        :return: The system prompt as a string.
        """
        pass

    @abstractmethod
    def _sub_init(self):
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
        text="rephrase_in_natural_language_\""

        if len(responses)>0:
            for i in range (len(responses)):
                response=responses[i]
                routine=routines[i]
                routine_name = routine.get("name", f"Routine {i+1}")
                if response.status_code == 200:
                    text += f"{routine_name} created successfully.\n"
                else:
                    text += f"Failed to create {routine_name} with status code {response.status_code}.\n"
        else:
            text += "Error: No routines were created.\n"
        text += "\""
        await self.process_customer_input(text, websocket=websocket, text_only=True)

    async def process_property_query(self, prop_query:list, websocket):
        if not prop_query or len(prop_query) == 0:
            return "No property query provided"
        try:
            if isinstance(prop_query[0], list): 
                prop_query = prop_query[0]
        except Exception as e:
            pass
        text = None
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
                text = "rephrase_in_natural_language_\" "
                if prop:
                    #text = f"rephrase_in_natural_language_\"{prop_name if prop_name else prop_id} for {device_name} is: {prop.formatted if prop.formatted else prop.value}\""
                    text += f"{device_name}: {prop.formatted if prop.formatted else prop.value}\n"
                    #await self.send_response(text, True, websocket)
                else:
                    print( f"Property {prop_id} not found for device {device_name}")
            else:
                print(f"No property ID provided for device {device_name}")

        if text is not None:
            text += "\""
            await self.process_customer_input(text, websocket=websocket, text_only=True)
            return  

    async def process_tool_responses(self, responses, websocket, original_messages=None):
        if not responses or len(responses) == 0:
            await self.send_response("Assistant didn't return anything", True, websocket)
            return None
        if not websocket:
            print(responses)
            return responses
        if isinstance(responses, list):
            output="rephrase_in_natural_language_\""
            for i in range(len(responses)):
                response = responses[i]
                original_message = original_messages[i] if original_messages and i < len(original_messages) else " "
                output += f"{original_message}{'successful' if response.status_code == 200 else 'failed with status code ' + str(response.status_code)}\n"

            output+="\""
            await self.process_customer_input(output, websocket=websocket, text_only=True)
        else:
            await self.process_customer_input(f"rephrase_in_natural_language_\"{'successful' if response.status_code == 200 else 'failed with status code ' + str(response.status_code)}\"", websocket=websocket, text_only=True)

        return responses

    async def send_commands(self, commands:list, websocket):
        responses = await self.nuCore.send_commands(commands)
        return await self.process_tool_responses(responses, websocket, original_messages=commands)
    
    async def process_json_tool_call(self, tool_call:dict, websocket):
        if not tool_call:
            return None
        try:
            type = tool_call.get("tool")
            if not type:
                return None
            elif type == "PropQuery":
                return await self.process_property_query(tool_call.get("args").get("queries"), websocket)
            elif type == "Command":
                return await self.send_commands(tool_call.get("args").get("commands"), websocket)
            elif type == "Routine":
                return await self.create_automation_routines(tool_call.get("args").get("routines"), websocket)
        except Exception as e:
            print(f"Error processing tool call: {e}")
            
        return None

    def _remove_duplicate_routines(self, routines:list):
        seen = set()
        unique_routines = []
        for routine in routines:
            routine_tuple = tuple(sorted(routine.items()))
            if routine_tuple not in seen:
                seen.add(routine_tuple)
                unique_routines.append(routine)
        return unique_routines

    async def process_json_tool_calls(self, tool_calls, websocket):
        if isinstance(tool_calls, dict):
            return await self.process_json_tool_call(tool_calls, websocket)
        elif isinstance(tool_calls, list):
            tool_calls = self._remove_duplicate_routines(tool_calls)
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
            tools = json.loads(full_response)
            return await self.process_json_tool_calls(tools, websocket)
        except Exception as ex:
            if not full_response:
                return ValueError("Invalid input to process_tool_call")
            else:
                print(f"Error parsing tool call JSON: {ex}")
                return None
            
    async def send_response(self, message, is_end=False, websocket=None):
        if not message:
            return
        if websocket:
            payload={
                "sender": "bot",
                "message": message,
                "end": "true" if is_end else "false"
            }
            await websocket.send_text(json.dumps(payload))
        print(message, end="", flush=True)

    async def process_customer_input(self, query:str, num_rag_results=5, rerank=True, websocket=None, text_only:bool=False):
        """
        Process the customer input using the underlying model with conversation state. 
        :param query: The customer input to process.
        :param num_rag_results: The number of RAG results to use for the actual query
        :param rerank: Whether to rerank the results.
        :param websocket: The websocket to send responses to (if any).
        :param text_only: Whether to return text only without processing tool calls
        """

        if not query:
            print("No query provided, exiting ...")
            return None
        
        rc = await self.__check_debug_mode__(query, websocket)
        if rc:
            return None

        device_docs = ""
        if not self.nuCore.load_devices(include_profiles=False):
                raise ValueError("Failed to load devices from NuCore. Please check your configuration.")

        rag = self.nuCore.format_nodes()
        if not rag:
            raise ValueError(f"Warning: No RAG documents found for node {self.nuCore.url}. Skipping.")

        rag_docs = rag["documents"]
        if not rag_docs:
            raise ValueError(f"Warning: No documents found in RAG for node {self.nuCore.url}. Skipping.")

        for rag_doc in rag_docs:
            device_docs += "\n" + rag_doc

        if self.device_docs is None:
            self.device_docs = device_docs

        changed = device_docs != self.device_docs
        if changed and len(self.message_history)>0:
            #reset message history if device docs have changed
            self.message_history = []
            self.device_docs = device_docs

        sprompt = self.system_prompt.strip()

        query = query.strip()
        if not query:
            await self.send_response("No query provided, exiting ...", True, websocket)
            return None

        if query.startswith("?"):
            query = "\n"+query[1:].strip()
        
        user_content = f"USER QUERY:{query}"
        if len(self.message_history) == 0 :
            self.message_history.append({"role": "system", "content": sprompt})
            user_content = f"DEVICE STRUCTURE:\n\n{device_docs}\n\n{user_content}"
            with open("/tmp/device_docs.txt", "w") as f:
                f.write(device_docs)
        # Add user message to history
        self.message_history.append({"role": "user", "content": user_content})
            
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

    async def main(self, welcome_message:str=None):
        if welcome_message:
            print(welcome_message)
        else:
            print("Welcome to NuCore AI Assistant!")
    
        print("Type 'quit' to exit")
        i=0
        
        while True:
            try:
                user_input = input("\nWhat can I do for you? > " if i==0 else "\n> ").strip()
                i+=1

                if not user_input:
                    print("Please enter a valid request")
                    continue

                if user_input.lower() == 'quit':
                    print("Goodbye!")
                    break

                print(f"\n>>>>>>>>>>\n")
                await self.process_customer_input(user_input, num_rag_results=3, rerank=False)
                print ("\n\n<<<<<<<<<<\n")
                
            except Exception as e:
                print(f"An error occurred: {e}")
                continue

