import os
import json
import httpx
import asyncio
from base_assistant import NuCoreBaseAssistant, get_parser_args


"""
Best option
build.cuda/bin/llama-server -m /home/michel/workspace/nucore/models/finetuned/qwen2.5-coder-dls-7b/qwen2.5-coder-dls-7b-Q4_K_M.gguf --jinja --host localhost -c 60000 --port 8013 -t 15  --n-gpu-layers 32 --batch-size 8192
"""

class NuCoreAssistant(NuCoreBaseAssistant):
    def __init__(self, args):
        super().__init__(args)

    def _get_system_prompt(self):
        # Assuming this code is inside your_package/module.py
        system_prompt = None
        prompts_path = os.path.join(os.getcwd(), "src", "prompts", "nucore.qwen.profile.prompt")
        with open(prompts_path, 'r', encoding='utf-8') as f:
            system_prompt = f.read().strip()
        return system_prompt

    def _check_for_duplicate_tool_call(self):
        return False, 0

    def _get_tools_prompt(self):
        return ""

    async def _sub_init(self):
        """
        Warm up the model by sending a dummy request without device structure

        """
        pass
#        sprompt = self.system_prompt.strip()
#        self.message_history.append({"role": "system", "content": sprompt})
#        self.message_history.append({"role": "user", "content": "Hello!"})
#        await self._process_customer_input(websocket=None, text_only=True)


    def _include_system_prompt_in_history(self) -> bool:
        """
        Whether to include the system prompt in the message history.
        :return: True if the system prompt should be included, False otherwise.
        """
        return True #already doing it in warm up --- IGNORE ---

    async def _process_customer_input(self, websocket, text_only:bool):
        """
        Process the customer input using OpenAI Responses API with conversation state.
        :param websocket: The websocket to send responses to (if any).
        :param text_only: Whether to return text only without processing tool calls
        """

        try:
            full_response = ""
            
            payload={
                "messages": self.message_history,
                "stream": True,
                'cache_prompt':True,
                "n_keep": -1,
                "temperature": 0.0,
                "max_tokens": 32000,
            }
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
                                    if text_only or self.debug_mode:
                                        await self.send_response(token_data, False, websocket if self.debug_mode else None)
                                    full_response += token_data

            if full_response is not None:
                if not text_only:
                    await self.process_tool_call(full_response, websocket, None, None)

            return full_response

        except Exception as e:
            print(f"An error occurred while processing the customer input: {e}")
            import traceback
            traceback.print_exc()
        return None 

if __name__ == "__main__":
    args = get_parser_args()
    assistant = NuCoreAssistant(args)
    asyncio.run(assistant.main(args))
