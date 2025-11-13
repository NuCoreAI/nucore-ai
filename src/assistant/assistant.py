import re
import requests, os
import json
import httpx
import asyncio, argparse

from nucore import NuCore 
from config import AIConfig

from assistant_session import AssistantSession


"""
Best option
build.cuda/bin/llama-server -m /home/michel/workspace/nucore/models/finetuned/qwen2.5-coder-dls-7b/qwen2.5-coder-dls-7b-Q4_K_M.gguf --jinja --host localhost -c 60000 --port 8013 -t 15  --n-gpu-layers 32 --batch-size 8192
"""
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
prompts_path = os.path.join(os.getcwd(), "src", "prompts", "nucore.system.prompt") 
with open(prompts_path, 'r', encoding='utf-8') as f:
    system_prompt = f.read().strip()

config = AIConfig()

class NuCoreAssistant:
    def __init__(self, args):
        self.sent_system_prompt=False
        self.session = AssistantSession()
        self.debug_mode = False
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

    async def create_automation_routine(self,routines:list, websocket):
        """
        Create automation routines in NuCore.
        :param routines: A list of routines to create.
        :return: The result of the routine creation.
        **for now, just a stub **
        """
        #return await self.nuCore.create_automation_routines(routines, websocket)
        await self.send_response(await self.get_random_success_message(), True, websocket)

    async def process_property_query(self, prop_query:list, websocket):
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
                    if websocket:
                        await self.send_response(f"{prop.formatted if prop.formatted else prop.value}", True, websocket)
                    text = f"\nNuCore: {prop_name if prop_name else prop_id} for {device_name} is: {prop.formatted if prop.formatted else prop.value}"
                    await self.send_response(text, True, websocket)
                else:
                    print( f"Property {prop_id} not found for device {property['device_id']}")
            else:
                print(f"No property ID provided for device {property['device_id']}")

    async def get_random_success_message(self):
        messages = [
            "All commands executed successfully!",
            "Commands completed without any issues.",
            "Everything ran smoothly, no errors detected.",
            "All operations were successful!",
            "Commands executed perfectly!",
            "Praise the code gods, all commands worked flawlessly!",
            "Success! All commands executed without a hitch.",
            "All systems go! Commands completed successfully.",
            "Mission accomplished! Commands ran without errors.",
            "Hooray! Every command executed successfully."
        ]
        import random
        return random.choice(messages)

    async def get_random_full_failure_message(self):
        messages = [
            "Unfortunately, all commands failed to execute.",
            "None of the commands were successful.",
            "All operations encountered errors.",
            "Regrettably, every command failed to run.",
            "Sadly, none of the commands worked out.",
            "Alas! All commands resulted in failure.",
            "Disappointingly, no commands executed successfully.",
            "All systems down! Commands failed to complete.",
            "Mission failed! Every command ran into issues.",
            "Oh no! None of the commands were successful."
        ]
        import random
        return random.choice(messages)
    
    async def get_random_partial_failure_message(self):
        messages = [
            "Some commands executed successfully, while others failed.",
            "A mix of successes and failures occurred during command execution.",
            "Certain operations were successful, but some encountered errors.",
            "Some commands ran smoothly, while others did not.",
            "A combination of successful and failed commands.",
            "Hooray! Some commands worked, but a few ran into issues.",
            "Partial success! Some commands executed without problems.",
            "Mixed results! Some commands completed successfully, others did not.",
            "Mission partially accomplished! Some commands ran into issues.",
            "Oh well! A few commands were successful, but some failed."
        ]
        import random
        return random.choice(messages)

    async def process_tool_responses(self, responses, websocket):
        if not responses:
            await self.send_response(await self.get_random_full_failure_message(), True, websocket)
            return None
        if not websocket:
            print(responses)
            return responses
        status_codes: list = []
        if isinstance(responses, list):
            for response in responses:
                status_codes.append(response.status_code)
        #now if all are 200, return a random success message
        if all(code == 200 for code in status_codes):
            await self.send_response(await self.get_random_success_message(), True, websocket)
        elif all(code != 200 for code in status_codes):
            await self.send_response(await self.get_random_full_failure_message(), True, websocket)
        else:
            await self.send_response(await self.get_random_partial_failure_message(), True, websocket)
        return responses

    async def send_commands(self, commands:list, websocket):
        responses = await self.nuCore.send_commands(commands)
        return await self.process_tool_responses(responses, websocket)
    
    async def process_clarify_device_tool_call(self, clarify:dict, websocket, user_response:str = None):
        return await self.session.process_clarify_device_tool_call(clarify, websocket, user_response)

    async def process_clarify_event_tool_call(self, websocket, clarify:dict):
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

    async def process_json_tool_call(self, tool_call:dict, websocket):
        if not tool_call:
            return None
        try:
            type = tool_call.get("tool")
            if not type:
                return None
            elif type == "PropQuery":
                self.session.redo_query = False
                return await self.process_property_query(tool_call.get("args").get("queries"), websocket)
            elif type == "Command":
                self.session.redo_query = False
                return await self.send_commands(tool_call.get("args").get("commands"), websocket)
            elif type == "ClarifyDevice":
                return await self.process_clarify_device_tool_call(tool_call.get("args").get("clarify"), websocket)
            elif type == "ClarifyEvent":
                return await self.process_clarify_event_tool_call(websocket, tool_call.get("args").get("clarify"))
            elif type == "Routine":
                self.session.redo_query = False
                return await self.create_automation_routine(tool_call.get("args").get("routines"), websocket)
        except Exception as e:
            print(f"Error processing tool call: {e}")
            
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
            tools = json.loads(full_response)
            return await self.process_json_tool_calls(tools, websocket)
        except Exception as ex:
            if not full_response or not begin_marker or not end_marker:
                return ValueError("Invalid input to process_tool_call")
            
    async def send_response(self, message, is_end=False, websocket=None):
        if not message:
            return
        if websocket:
            payload={
                "sender": "bot",
                "message": message,
                "end": "true" if is_end else "false"
            }
            #print(payload)
            await websocket.send_text(json.dumps(payload))
        print(message, end="", flush=True)

    async def send_user_content_to_llm(self, user_content, websocket=None):
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
        await self.send_response(response.json()["choices"][0]["message"]["content"], True, websocket)
        return None

    async def process_customer_input(self, query:str, num_rag_results=5, rerank=True, websocket=None):
        """
        Process the customer input by sending it to the AI model and handling the response.
        :param query: The customer input to process.
        :param num_rag_results: The number of RAG results to use for the actual query
        :param rerank: Whether to rerank the results.
        """

        if not query:
            print("No query provided, exiting ...")
            return None
        
        rc = await self.__check_debug_mode__(query, websocket)
        if rc:
            return None
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
      #  with open(f"/tmp/ai.prompt", "w") as f:
      #      f.write(sprompt)

        system_message = {
            "role": "system",
            "content": sprompt
        }
        query= query.strip()
        if not query:
            await self.send_response("No query provided, exiting ...", True, websocket)
            return None

        if query.startswith("?"):
            query = "\n"+query[1:].strip()  # Remove leading '?' if presented
#        else:
            # This is a code-only query, so we don't need to send the system prompt
#            query = f"**code-only** **no-explanation**\n{query}"

        context = None
        if self.session.has_messages():
            context = self.session.get_context()
        user_message = {
            "role": "user",
            "content": f"\n\nDEVICE STRUCTURE:\n\n{device_docs}\n\nUSER QUERY:{query}\n\n<END_OF_QUERY>" if not context 
                else f"\n\nDEVICE STRUCTURE:\n\n{device_docs}\n\n{context}\n\nUSER QUERY:{query}\n\n<END_OF_QUERY>", 
        }
        self.session.last_user_query=query

        print (f"\n\n*********************System Prompt:********************\n\n{user_message['content']}\n\n")

        #first use rag for relevant documents
        #rag_results = self.nuCore.query(query, num_rag_results, rerank)
        #context = None
        #if rag_results:
        #    context = "***Relevant documents***\n"
        #    for document in rag_results['documents']:
        #        context += f"---\n{document}"
#
#        query = query.strip() if not context else f"{context.strip()}\n\n Customer Question: {query.strip()}"

#        print (f"\n\n*********************Customer Query: {query}********************\n\n")

#        if rag_results:
#            print(f"\n\n*********************Top 5 Query Results:(Rerank = {rerank})********************\n\n")
#            for i in range(len(rag_results['ids'])):
#                print(f"{i+1}. {rag_results['ids'][i]} - {rag_results['distances'][i]} - {rag_results['relevance_scores'][i]}")
#            print("\n\n***************************************************************\n\n")

        #if not self.sent_system_prompt:
        messages.append(system_message)
        
        self.sent_system_prompt = True

        messages.append(user_message)
        # Step 1: Get tool call
        payload={
            "messages": messages,
            "stream": True,
            'cache_prompt':True,
            "n_keep": -1,
            "temperature": 0.0,
        #    "top_p":0.9,
            "stop":["\n\n<END_OF_QUERY>"] ,
            "max_tokens": 16000,
        }
        full_response = ""
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream("POST", self.__model_url__, timeout=100, json=payload, headers={
                    "Authorization": f"Bearer {self.__model_auth_token__}" if self.__model_auth_token__ else "",
                }) as response:
                    if response.status_code == 401 or response.status_code == 403:
                        print(f"Authorization token is invalid or expired. You need to refresh it.")
                        return None
                    elif response.status_code == 500:
                        print(f"Internal server error. Please try again later (most probably the authorization token is invalid or expired).")
                        return None
                    else:
                        async for line in response.aiter_lines():
                            if line.startswith("data: "):
                                token_data = line[len(b"data: "):]
                                try:
                                    finish_reason = json.loads(token_data.strip())['choices'][0]['finish_reason']
                                    if finish_reason == "stop":
                                        break
                                    token_data = json.loads(token_data.strip())['choices'][0]['delta'].get('content', '')
                                except json.JSONDecodeError:
                                    continue
                                if token_data:
                                    # Print the token data as it arrives
                                    if isinstance(token_data, bytes):
                                        token_data = token_data.decode("utf-8")
                                    await self.send_response(token_data, False, websocket if self.debug_mode else None)
                                    #full_response += token_data  # Collect the token data
                                    full_response += token_data

            # now parse the full response and look for blocks between __NUCORE_COMMAND_BEGIN__ and __NUCORE_COMMAND_END__. 
            # convert the blocks to json and add to list
            #with open("nucore_out.json", "w") as f: 
            #    f.write(full_response)
            if websocket: 
                if self.debug_mode:
                    await self.send_response("\r\n***\r\n", False, websocket)
            await self.process_tool_call(full_response, websocket, None, None)


        except Exception as e:
            print(f"An error occurred while processing the customer input: {e}")
        return None 
    

async def main(args):
    print("Welcome to NuCore AI Assistant!")
    print("Type 'quit' to exit")
    assistant = NuCoreAssistant(args)  # Replace with actual websocket connection if needed
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

            while True:
                await assistant.process_customer_input(user_input, num_rag_results=3, rerank=False)
                if not assistant.session.redo_query:
                    break
            print ("\n\n<<<<<<<<<<\n")
            
        except Exception as e:
            print(f"An error occurred: {e}")
            continue

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

if __name__ == "__main__":
    args = get_parser_args()
    asyncio.run(main(args))

    