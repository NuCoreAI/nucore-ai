# implement claude assistant using Messages API

import os
import os
from pathlib import Path
import asyncio
from typing import Tuple

from anthropic import AsyncAnthropic
from base_assistant import NuCoreBaseAssistant, get_parser_args, DEFAULT_TOOL_CALL_TIME_WINDOW_SECONDS
from prompt_mgr import NuCorePrompt


SECRETS_DIR = Path(os.path.join(os.getcwd(), "secrets") )
if not SECRETS_DIR.exists():
    raise FileNotFoundError(f"Secrets directory {SECRETS_DIR} does not exist. Please create it and add your Anthropic API key.")
# Load the Anthropic API key from the secrets file
if not (SECRETS_DIR / "keys.py").exists():
    raise FileNotFoundError(f"Secrets file {SECRETS_DIR / 'keys.py'} does not exist. Please create it and add your Anthropic API key.")

exec(open(SECRETS_DIR / "keys.py").read())  # This will set ANTHROPIC_API_KEY


class NuCoreAssistant(NuCoreBaseAssistant):
    def __init__(self, args):
        super().__init__(args)

    def _get_prompt_config_path(self):
        # Assuming this code is inside your_package/module.py
        system_prompt="claude_config.json"
        return os.path.join(os.getcwd(), "src", "prompts", system_prompt)

    def _check_for_duplicate_tool_call(self) -> Tuple[bool, int]: 
        return False, DEFAULT_TOOL_CALL_TIME_WINDOW_SECONDS 
 
    async def _sub_init(self):
        self.client = AsyncAnthropic(api_key=CLAUDE_API_KEY)

    def _include_system_prompt_in_history(self) -> bool:
        """
        Whether to include the system prompt in the message history.
        :return: True if the system prompt should be included, False otherwise.
        """
        return False

    async def _process_customer_input(self, prompt:NuCorePrompt, websocket, text_only:bool)-> str:
        """
        Process the customer input using Claude Messages API with conversation state.
        :param prompt: The prompt object containing message history and other details.
        :param websocket: The websocket to send responses to (if any).
        :param text_only: Whether to return text only without processing tool calls
        """

        try:
            full_response = ""
            
            # Convert message history to Claude format
            # Claude expects alternating user/assistant messages (no "system" role)
            # Create streaming message
            async with self.client.messages.stream(
                model="claude-sonnet-4-20250514",
                max_tokens=16000,
                temperature=1.0,
                tools=self.orchestrator.get_router_tools(),
                messages=self.message_history
            ) as stream:
                first_line = True
                async for event in stream:
                    # Handle different event types
                    if event.type == "content_block_delta":
                        if hasattr(event.delta, "text"):
                            delta_text = event.delta.text
                            if not delta_text or delta_text == "" or delta_text.isspace():
                                continue
                            full_response += delta_text
                            
                            if first_line:
                                if delta_text[0] != '{':
                                    text_only = True
                                if text_only or self.debug_mode:
                                    await self.send_response(f"\r\n***\r\n", False, websocket)
                            first_line = False
                        elif event.delta.type == "input_json":
                            delta_text=event.delta.partial_json
                            if self.debug_mode:
                                await self.send_response(f"{delta_text}", False, websocket)
                            full_response += delta_text
                    elif event.type == "message_stop":
                        if full_response:
                            if not text_only:
                                rc = await self.process_tool_call(full_response, websocket, None, None)
                            await self.send_response("\n", False, websocket)
            
            return full_response

        except Exception as e:
            print(f"An error occurred while processing the customer input: {e}")
            import traceback
            traceback.print_exc()
        return None 

if __name__ == "__main__":
    args = get_parser_args()
    claude_assistant = NuCoreAssistant(args)
    asyncio.run(claude_assistant.main(welcome_message="Welcome to NuCore Claude Assistant."))
