import re
import requests, os
import json
import httpx
import asyncio, argparse

from nucore import NuCore 
from config import AIConfig

"""
Best option
build.cuda/bin/llama-server -m /home/michel/workspace/nucore/models/finetuned/qwen2.5-coder-dls-7b/qwen2.5-coder-dls-7b-Q4_K_M.gguf --jinja --host localhost -c 60000 --port 8013 -t 15  --n-gpu-layers 32 --batch-size 8192
"""
from importlib.resources import files

# Assuming this code is inside your_package/module.py
data_path = files('assistant').joinpath('nucore.system.prompt')

with data_path.open('r', encoding='utf-8') as f:
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
            backend_password=args.password
        )
        model_url = args.model_url+"/v1/chat/completion" if args.model_url else config.getModelURL()
        if not model_url:
            raise ValueError("Model URL is required to initialize NuCoreAssistant")
        self.__model_url__ = model_url
        self.__model_auth_token__ = args.model_auth_token if args.model_auth_token else None
        print (self.__model_url__)
        self.nuCore.load()

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
        return self.nuCore.create_automation_routines(routines)
    
    async def process_property_query(self, prop_query:list):
        if not prop_query or len(prop_query) == 0:
            return "No property query provided"
        for property in prop_query:
            # Process the property query
            device_id = property.get('device_id')
            if not device_id:
                print(f"No device ID provided for property query: {property}")
                continue
            properties = await self.nuCore.get_properties(device_id)
            if not properties:
                print(f"No properties found for device {property['device_id']}")
                continue
            prop_id = property.get('property_id')
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


    async def process_tool_call(self,full_response:str, begin_marker, end_marker):
        if not full_response or not begin_marker or not end_marker:
            return None

        #we need an ordered command list to process. The order is important:
        # first command must run first and second next etc.
        parameters = [] #ordered set of commands 

        command_pattern = re.compile(rf'{begin_marker}(.*?){end_marker}', re.DOTALL)
        matches = command_pattern.findall(full_response)

        for match in matches:
            try:
                # Remove any comments that start with //, # or /* and end with */
                match = re.sub(r'//.*?$|#.*?$|/\*.*?\*/', '', match, flags=re.MULTILINE)
                # Remove any leading or trailing whitespace
                match = match.strip()
                if not match:
                    continue
                # Parse the JSON block
                parameter_json = json.loads(match.strip())
                parameters.append(parameter_json)
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON from nucore block: {e}")
                return None
            
        if len(parameters) > 0:
            if begin_marker == "__BEGIN_NUCORE_COMMAND__":
                return await self.nuCore.send_commands(parameters)
        
            elif begin_marker == "__BEGIN_NUCORE_PROPERTY_QUERY__":
                return await self.process_property_query(parameters) 
        
        return None

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

        sprompt = system_prompt.replace("{device_docs}", device_docs)
        sprompt.strip()
        with open(f"/tmp/ai.prompt", "w") as f:
            f.write(sprompt)

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
        else:
            # This is a code-only query, so we don't need to send the system prompt
            query = f"**code-only** **no-explanation**\n{query}"

        user_message = {
            "role": "user",
            "content": f"USER QUERY:{query}"
        }

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
            "max_tokens": 60_000,
        }
        full_response = ""
        try:
            with httpx.stream("POST", self.__model_url__, timeout=100, json=payload,headers={
                "Authorization": f"Bearer {self.__model_auth_token__}" if self.__model_auth_token__ else "",
            }) as response:
                if response.status_code == 401 or response.status_code == 403:
                    print(f"Authorization token is invalid or expired. You need to refresh it.")
                    return None
                elif response.status_code == 500:
                    print(f"Internal server error. Please try again later (most probably the authorization token is invalid or expired).")
                    return None
                else:
                    for line in response.iter_lines():
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
                                await self.send_response(token_data, False)
                                #full_response += token_data  # Collect the token data
                                full_response += token_data


            # now parse the full response and look for blocks between __NUCORE_COMMAND_BEGIN__ and __NUCORE_COMMAND_END__. 
            # convert the blocks to json and add to list
            await self.process_tool_call(full_response, "__BEGIN_NUCORE_COMMAND__", "__END_NUCORE_COMMAND__")
            await self.process_tool_call(full_response, "__BEGIN_NUCORE_PROPERTY_QUERY__", "__END_NUCORE_PROPERTY_QUERY__")

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


    args = parser.parse_args()
    asyncio.run(main(args))

    