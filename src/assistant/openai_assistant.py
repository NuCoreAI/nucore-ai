# implement openai assistant using Responses API

import re, os
import os
from pathlib import Path
import json
import asyncio
from typing import Tuple


from openai import AsyncOpenAI
from base_assistant import NuCoreBaseAssistant, get_parser_args, DEFAULT_TOOL_CALL_TIME_WINDOW_SECONDS
from nucore import PromptFormatTypes 



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

    def _get_max_context_size(self) ->int:
        """
        Get the maximum context size for the model.
        :return: The maximum context size as an integer.
        """
        return 64000

    def _get_prompt_config_path(self):
        # Assuming this code is inside your_package/module.py
        system_prompt = "openai_profile_config.json" if self.prompt_type == PromptFormatTypes.PROFILE else "openai_config.json"
        return os.path.join(os.getcwd(), "src", "prompts", system_prompt)

    def _check_for_duplicate_tool_call(self) -> Tuple[bool, int]: 
        return False, DEFAULT_TOOL_CALL_TIME_WINDOW_SECONDS 
    
    async def _sub_init(self):
        self.client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    def _include_system_prompt_in_history(self) -> bool:
        """
        Whether to include the system prompt in the message history.
        :return: True if the system prompt should be included, False otherwise.
        """
        return True
    async def _process_customer_input(self, websocket, text_only:bool):
        """
        Process the customer input using OpenAI Responses API with conversation state.
        :param websocket: The websocket to send responses to (if any).
        :param text_only: Whether to return text only without processing tool calls
        """

        try:
            full_response = ""
            
            # Create response - pass previous messages as additional_messages
            stream = await self.client.responses.create(
                model="gpt-4.1-mini",
#                model="ft:gpt-4.1-mini-2025-04-14:universal-devices:nucore13:Cmy5unf9",
                # instructions=self.system_prompt,
                input=self.message_history,
                max_tool_calls=3,
                parallel_tool_calls=False,
                temperature=1.0,
                tools=self.orchestrator.get_router_tools(),
                stream=True
            )
            first_line = True 
            function_args = ""
            async for event in stream:
            # Text chunks as they arrive
                if event.type == "response.function_call_arguments.delta":
                    # Accumulate function arguments
                    function_args += event.delta
                    full_response += event.delta
                    if self.debug_mode:
                        await self.send_response(f"{event.delta}", False, websocket)
                    # Function call completed
                elif event.type == "response.function_call_arguments.done":
                    # event.name contains the function name
                    # event.arguments contains complete JSON string
                    try:
                        #function_name = event.name
                        function_args = json.loads(event.arguments)
                    except json.JSONDecodeError:
                        pass
                elif event.type == "response.output_text.delta":
                    if not event.delta or event.delta == "": # or event.delta.isspace():
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
                    if not text_only and len(full_response) > 30:
                        try:
                            json.loads(full_response)
                            #await self.process_tool_call(full_response, websocket, None, None)
                            break 
                        except json.JSONDecodeError:
                            pass
            # End of response
                elif event.type == "response.completed":
                    if full_response is not None and full_response != "":
#                        if not text_only:
#                           await self.process_tool_call(full_response, websocket, None, None)
#                        if self.debug_mode or text_only:
#                            await self.send_response("\r\n***\r\n", False, websocket)
#                        else:
                        break

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
