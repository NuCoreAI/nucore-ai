# implement openai assistant here

import re
import requests, os
import json
import asyncio, argparse

from nucore import NuCore 
from config import AIConfig

from openai import OpenAI


from importlib.resources import files

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

#print current working directory
print(os.getcwd())

# Assuming this code is inside your_package/module.py
prompts_path = os.path.join(os.getcwd(), "src", "prompts", "nucore.openai.system.prompt") 
with open(prompts_path, 'r', encoding='utf-8') as f:
    system_prompt = f.read().strip()


config = AIConfig()

class NuCoreAssistant:
    def __init__(self, args, websocket=None):
        self.websocket=websocket
        self.sent_system_prompt=False
        if not args:
            raise ValueError("Arguments are required to initialize NuCoreAssistant")
        self.nuCore = NuCore(
            collection_path=args.collection_path if args.collection_path else os.path.join(os.path.expanduser("~"), ".nucore_db"),
            collection_name="nucore.assistant",
            backend_url=args.url,
            backend_username=args.username,
            backend_password=args.password,
            embedder_url=args.embedder_url if args.embedder_url else config.getEmbedderURL(),
            reranker_url=args.reranker_url if args.reranker_url else config.getRerankerURL()
        )
        if not self.nuCore:
            raise ValueError("Failed to initialize NuCore. Please check your configuration."
        )
        model_url = args.model_url if args.model_url else config.getModelURL()
        if not model_url:
            raise ValueError("Model URL is required to initialize NuCoreAssistant")
        self.__model_url__ = model_url
        self.__model_auth_token__ = args.model_auth_token if args.model_auth_token else None
        print (self.__model_url__)
        self.nuCore.load()
        ### us the following to include rags and embed them and rerank them.
        #self.nuCore.load(include_rag_docs=True, static_docs_path="/tmp/embeddings", embed=True)

    def set_remote_model_access_token(self, token: str):
        """
        You are responsible for refreshing the access token
        Set the remote model access token.
        :param token: The access token to set.
        """
        self.__model_auth_token__ = token

    async def create_automation_routine(self,routines:list):
        """
        Create automation routines in NuCore.
        :param routines: A list of routines to create.
        :return: The result of the routine creation.
        **for now, just a stub **
        """
        return await self.nuCore.create_automation_routines(routines)
    
    async def process_property_query(self, prop_query:list):
        if not prop_query or len(prop_query) == 0:
            return "No property query provided"
        try:
            if isinstance(prop_query[0], list): 
                prop_query = prop_query[0]
        except Exception as e:
            pass

        for property in prop_query:
            # Process the property query
            device_id = property.get('device') or property.get('device_id')
            if not device_id:
                print(f"No device ID provided for property query: {property}")
                continue
            properties = await self.nuCore.get_properties(device_id)
            if not properties:
                print(f"No properties found for device {property['device_id']}")
                continue
            prop_id = property.get('property') or property.get('property_id')
            prop_name = property.get('property_name')
            device_name = self.nuCore.get_device_name(device_id)
            if not device_name:
                device_name = device_id
            if prop_id:
                prop = properties.get(prop_id)
                if prop:
                    text = f"\nNuCore: {prop_name if prop_name else prop_id} for {device_name} is: {prop.formatted if prop.formatted else prop.value}"
                    #await self.send_user_content_to_llm(text)
                    await self.send_response(text, True)
                else:
                    print( f"Property {prop_id} not found for device {property['device_id']}")
            else:
                print(f"No property ID provided for device {property['device_id']}")

    async def process_clarify_device_tool_call(self, clarify:dict):
        """
        {"tool":"ClarifyDevice","args":{"clarify":{
            "question": "natural language question",
            "options": [
                { "name": "name of possible device 1"},
                { "name": "name of possible device 2"},
                ...
            ]}
            }}
        """
        return None

    async def process_clarify_event_tool_call(self, clarify:dict):
        """
        {"tool":"ClarifyEvent","args":{"clarify":{
            "question": "natural language question",
            "options": [
                { "name": "name of possible event 1"},
                { "name": "name of possible event 2"},
                ...
            ]}
            }}
        """
        return None

    async def process_json_tool_call(self, tool_call:dict):
        if not tool_call:
            return None
        try:
            type = tool_call.get("tool")
            if not type:
                return None
            elif type == "PropQuery":
                return await self.process_property_query(tool_call.get("args").get("queries"))
            elif type == "Command":
                return await self.nuCore.send_commands(tool_call.get("args").get("commands"))
            elif type == "ClarifyDevice":
                return await self.process_clarify_device_tool_call(tool_call.get("args").get("clarify"))
            elif type == "ClarifyEvent":
                return await self.process_clarify_event_tool_call(tool_call.get("args").get("clarify"))
            elif type == "Routine":
                return await self.create_automation_routine(tool_call.get("args").get("routines"))
        except Exception as e:
            print(f"Error processing tool call: {e}")
            
        return None

    async def process_json_tool_calls(self, tool_calls):
        if isinstance(tool_calls, dict):
            return await self.process_json_tool_call(tool_calls)
        elif isinstance(tool_calls, list):
            for tool_call in tool_calls:
                return await self.process_json_tool_call(tool_call)
        return None 

    async def process_tool_call(self,full_response:str, begin_marker, end_marker):
        if not full_response: 
            return None

        tools = None
        try:
            tools = json.loads(full_response)
            return await self.process_json_tool_calls(tools)
        except Exception as ex:
            if not full_response or not begin_marker or not end_marker:
                return ValueError("Invalid input to process_tool_call")
            
    async def send_response(self, message, is_end=False):
        if not message:
            return
        if self.websocket:
            await self.websocket.send_json({
                "sender": "bot",
                "message": message,
                "end": 'true' if is_end else 'false'
            })
        else:
            print(message, end="", flush=True)

    async def send_user_content_to_llm(self, user_content):
        """
        Send user content to the LLM for processing.
        :param user_content: The content provided by the user.
        """
        if not user_content:
            print("No user content provided, exiting ...")
            return
        user_message = {
            "role": "user",
            "content": f"{user_content.strip()}"
        }
        messages = [user_message]
        payload={
            "messages": messages,
            "stream": False,
            "temperature": 2.0,
            "max_tokens": 60_000,
        }

        response = requests.post(self.__model_url__, json=payload, headers={
            "Authorization": f"Bearer {self.__model_auth_token__}" if self.__model_auth_token__ else "",
        })
        response.raise_for_status()
        await self.send_response(response.json()["choices"][0]["message"]["content"])
        return None

    async def process_customer_input(self, query:str, num_rag_results=5, rerank=True):
        """
        Process the customer input by sending it to the AI model and handling the response.
        :param query: The customer input to process.
        :param num_rag_results: The number of RAG results to use for the actual query
        :param rerank: Whether to rerank the results.
        """

        if not query:
            print("No query provided, exiting ...")
        messages =[]

        device_docs = ""
        if not self.nuCore.load_devices(include_profiles=False):
                raise ValueError("Failed to load devices from NuCore. Please check your configuration.")

        # Load RAG documents
        #if not self.nuCore.load_rag_docs(dump=False):
        #    raise ValueError("Failed to load RAG documents from NuCore. Please check your configuration.")
        rag = self.nuCore.format_nodes()
        if not rag:
            raise ValueError(f"Warning: No RAG documents found for node {self.nuCore.url}. Skipping.")

        rag_docs = rag["documents"]
        if not rag_docs:
            raise ValueError(f"Warning: No documents found in RAG for node {self.nuCore.url}. Skipping.")

        for rag_doc in rag_docs:
            device_docs += "\n" + rag_doc

        #sprompt = system_prompt.replace("{device_docs}", device_docs)
        #sprompt.strip()
        sprompt = system_prompt.strip()

        system_message = {
            "role": "system",
            "content": sprompt
        }
        query= query.strip()
        if not query:
            await self.send_response("No query provided, exiting ...", True)
            return None

        if query.startswith("?"):
            query = "\n"+query[1:].strip()  # Remove leading '?' if presented

        user_message = {
            "role": "user",
            "content": f"DEVICE STRUCTURE:\n\n{device_docs}\n\nUSER QUERY:{query}\n\n<END_OF_QUERY>" 
        }

        #if self.sent_system_prompt == False:
        messages.append(system_message)
        self.sent_system_prompt = True

        messages.append(user_message)
        # Step 1: Get tool call
        try:
            full_response = ""
            with client.chat.completions.stream(
                #model="gpt-4.1-nano",  # or gpt-4o, gpt-4.1-mini for slightly higher quality
                model="ft:gpt-4.1-mini-2025-04-14:universal-devices:nucore-13:CSfIc6HZ",  # or gpt-4o, gpt-4.1-mini for slightly higher quality
                messages=messages,
                temperature=1.0,
                top_p=0.9,
                max_tokens=16000,               # tight upper bound
                tool_choice=None,
                stop=["\n\n<END_OF_QUERY>"] 
            ) as stream:
                for event in stream:
                    t = event.type
                    if t in ("chunk", "delta", "response.output_text.delta"):
                        # try the new shape first
                        text = None
                        if hasattr(event, "chunk"):
                            choice = event.chunk.choices[0]
                            delta = choice.delta
                            if delta and delta.content:
                                text = delta.content    
                        elif hasattr(event, "delta"):
                            text = getattr(event, "delta", None)
                        if text:
                            if isinstance(text, bytes):
                                text = text.decode("utf-8")
                            full_response += text
                            await self.send_response(text, False)
                    elif t in ("completed", "response.completed"):
                        await self.send_response("", True)  # indicate end of response
                    elif t == "error":
                        print(f"Error from model: {event.error}")
                
            await self.process_tool_call(full_response, None, None)
            with open("nucore_out.json", "w") as f: 
                f.write(full_response)

        except Exception as e:
            print(f"An error occurred while processing the customer input: {e}")
        return None 
    

async def main(args):
    print("Welcome to NuCore AI Assistant!")
    print("Type 'quit' to exit")
    assistant = NuCoreAssistant(args, websocket=None)  # Replace with actual websocket connection if needed
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
                
            await assistant.process_customer_input(user_input, num_rag_results=3, rerank=False)
            print ("\n\n<<<<<<<<<<\n")
            
        except Exception as e:
            print(f"An error occurred: {e}")
            continue

if __name__ == "__main__":
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

    args = parser.parse_args()
    asyncio.run(main(args))

    
