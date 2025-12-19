# implement openai assistant using Responses API

import re, os
import os
from pathlib import Path
import json
import asyncio, argparse

from nucore import NuCore 

from openai import AsyncOpenAI
from base_assistant import NuCoreBaseAssistant, get_parser_args


SECRETS_DIR = Path(os.path.join(os.getcwd(), "secrets") )
if not SECRETS_DIR.exists():
    raise FileNotFoundError(f"Secrets directory {SECRETS_DIR} does not exist. Please create it and add your OpenAI API key.")
# Load the OpenAI API key from the secrets file
if not (SECRETS_DIR / "keys.py").exists():
    raise FileNotFoundError(f"Secrets file {SECRETS_DIR / 'keys.py'} does not exist. Please create it and add your OpenAI API key.")

exec(open(SECRETS_DIR / "keys.py").read())  # This will set OPENAI_API_KEY


class NuCoreAssistant(NuCoreBaseAssistant):
    def __init__(self, args):
        super().__init__(args)

    def _get_system_prompt(self):
        # Assuming this code is inside your_package/module.py
        system_prompt = None
        prompts_path = os.path.join(os.getcwd(), "src", "prompts", "nucore.openai.prompt")
        with open(prompts_path, 'r', encoding='utf-8') as f:
            system_prompt = f.read().strip()
        return system_prompt
    
    def _sub_init(self):
        self.client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    def _include_system_prompt_in_history(self) -> bool:
        """
        Whether to include the system prompt in the message history.
        :return: True if the system prompt should be included, False otherwise.
        """
        return False
    async def _process_customer_input(self, num_rag_results:int, rerank:bool, websocket, text_only:bool):
        """
        Process the customer input using OpenAI Responses API with conversation state.
        :param num_rag_results: The number of RAG results to use for the actual query
        :param rerank: Whether to rerank the results.
        :param websocket: The websocket to send responses to (if any).
        :param text_only: Whether to return text only without processing tool calls
        """

        try:
            full_response = ""
            
            # Create response - pass previous messages as additional_messages
            stream = await self.client.responses.create(
                model="gpt-4.1-mini",
#                model="ft:gpt-4.1-mini-2025-04-14:universal-devices:nucore13:Cmy5unf9",
                instructions=self.system_prompt,
                input=self.message_history,
                temperature=1.0,
                stream=True
            )
            first_line = True 
            async for event in stream:
            # Text chunks as they arrive
                if event.type == "response.output_text.delta":
                    if not event.delta or event.delta == "" or event.delta.isspace():
                        continue
                    full_response += event.delta
                    if first_line:
                        if event.delta[0] != '{':
                            text_only = True
                        if text_only or self.debug_mode:
                            await self.send_response(f"\r\n***\r\n", False, websocket)
                    first_line = False
                    if text_only or self.debug_mode:
                        await self.send_response(f"{event.delta}", False, websocket)
            # End of response
                elif event.type == "response.completed":
                    if full_response:
                        if not text_only:
                            rc = await self.process_tool_call(full_response, websocket, None, None)
#                        if self.debug_mode or text_only:
#                            await self.send_response("\r\n***\r\n", False, websocket)
#                        else:
                        await self.send_response("\n", False, websocket)
            return full_response

        except Exception as e:
            print(f"An error occurred while processing the customer input: {e}")
            import traceback
            traceback.print_exc()
        return None 

if __name__ == "__main__":
    args = get_parser_args()
    openai_assistant = NuCoreAssistant(args)
    asyncio.run(openai_assistant.main(welcome_message="Welcome to NuCore OpenAI Assistant."))
