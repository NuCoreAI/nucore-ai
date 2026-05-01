from __future__ import annotations
from typing import Any

from intent_handler.stream_handler import StreamHandler

class TranslateAgentOutputStreamHandler(StreamHandler):
    """
        Stream handler for Agent Output Translation intent. This stream handler processes chunks of text from the LLM response in real-time, counting the number of chunks received and logging each chunk as it arrives. The default behavior is to log the chunk text using the module logger, but you can override the `handle_stream_chunk` method to implement custom streaming behavior (e.g. updating a UI component, sending data over a WebSocket, etc.).   
        This is an example of how to implement a stream handler that processes chunks of text from the LLM response in real-time. 
        You can customize the behavior of this stream handler by overriding the `handle_stream_chunk` method.
    """
    def handle_stream_chunk(self, chunk: str) -> Any:
        """
        Default stream handler that counts chunks and prints them to stdout.
        You can override this method to implement custom streaming behavior (e.g. progress bars, UI updates, etc.)
        :param chunk: A string chunk from the LLM stream.
        :return: Any value to be returned from the stream handler (optional).
        """
        if not chunk:
            return
        self.stream_state["chunks"] += 1
        print(chunk, end="", flush=True)
    