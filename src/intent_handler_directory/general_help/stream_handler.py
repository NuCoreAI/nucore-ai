from __future__ import annotations
from typing import Any
from utils import get_logger

logger = get_logger(__name__)


from intent_handler.stream_handler import StreamHandler

class GeneralHelpStreamHandler(StreamHandler):
    """
        Stream handler for the General Help intent. 
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
        logger.info(chunk+"")
        #print(chunk, end="", flush=True)
    