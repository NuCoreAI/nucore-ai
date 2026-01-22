import os
import json
import httpx
import asyncio
from base_assistant import NuCoreBaseAssistant, get_parser_args
from nucore import PromptFormatTypes 
from prompt_mgr import NuCorePrompt


"""
Best option
build.cuda/bin/llama-server -m /home/michel/workspace/nucore/models/finetuned/qwen2.5-coder-dls-7b/qwen2.5-coder-dls-7b-Q4_K_M.gguf --jinja --host localhost -c 60000 --port 8013 -t 15  --n-gpu-layers 32 --batch-size 8192
"""

class NuCoreAssistant(NuCoreBaseAssistant):
    def __init__(self, args):
        super().__init__(args)

    def _get_prompt_config_path(self):
        # Assuming this code is inside your_package/module.py
        system_prompt =  "qwen_profile_config.json" if self.prompt_type == PromptFormatTypes.PROFILE else "qwen_config.json"
        return os.path.join(os.getcwd(), "src", "prompts", system_prompt)

    def _get_max_context_size(self) ->int:
        """
        Get the maximum context size for the model.
        :return: The maximum context size as an integer.
        """
        return 64000

    def _check_for_duplicate_tool_call(self):
        return False, 0

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
    
    async def _process_customer_input(self, prompt:NuCorePrompt, websocket, text_only:bool)-> str:
        """
        Process the customer input using OpenAI Responses API with conversation state.
        :param prompt: The prompt object containing message history and other details.
        :param websocket: The websocket to send responses to (if any).
        :param text_only: Whether to return text only without processing tool calls
        """

        try:
            full_response = ""
            first_line=True
            
            payload={
                "messages": prompt.message_history,
                "tools": prompt.tools,
                "stream": True,
                'cache_prompt':True,
                "n_keep": -1,
                "temperature": 0.0,
                "top_p": 1.0,
            #    "max_tokens": 13000,
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
                    elif response.status_code == 400:
                        error_detail = await response.aread()
                        try:
                            error_details = json.loads(error_detail.decode('utf-8'))
                            type = error_details.get("error", "").get("type", "")
                            if type == "exceed_context_size_error":
                                print(f"reached max context size limit, trimming history ...")
                                await self._trim_and_redo_last_message(websocket=websocket, text_only=text_only)
                        except json.JSONDecodeError:
                            pass
                        print(f"Bad request: {error_detail.decode('utf-8')}")
                        return None
                    else:
                        async for line in response.aiter_lines():
                            if first_line:
                                if text_only or self.debug_mode:
                                    await self.send_response(f"\r\n", False, websocket)
                                first_line = False
                            if line.startswith("data: "):
                                token_data = line[len(b"data: "):]
                                data=None
                                try:
                                    finish_reason = json.loads(token_data.strip())['choices'][0]['finish_reason']
                                    if finish_reason == "stop" or finish_reason == "tool_calls":
                                        break
                                    data = json.loads(token_data.strip())['choices'][0]['delta'].get('content', '')
                                    if (not data):
                                        try:
                                            data = json.loads(token_data.strip())['choices'][0]['delta'].get('tool_calls', '')
                                            if data:
                                                tool_calls = data[0]
                                                if tool_calls: 
                                                    function = tool_calls.get("function")
                                                    data = function.get("arguments")
                                        except json.JSONDecodeError:
                                            pass
                                except json.JSONDecodeError:
                                    continue
                                if data:
                                    # Print the token data as it arrives
                                    if isinstance(data, bytes):
                                        data = data.decode("utf-8")
                                    if text_only or self.debug_mode:
                                        await self.send_response(data, False, websocket if self.debug_mode else None)
                                    full_response += data

#            if full_response is not None:
#                tool_call = self._get_tool_call(full_response)
#                if tool_call is not None and not text_only:
#                    await self.process_tool_call(tool_call, websocket, None, None)

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
