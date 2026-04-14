from abc import ABC, abstractmethod
from typing import Any

class StreamHandler(ABC):
    def __init__(self, stream_state: dict[str, int]=None):
        self.stream_state = {"chunks": 0} if stream_state is None else stream_state

    def reset_stream_state(self) -> None:
        self.stream_state["chunks"] = 0

    def get_stream_chunk_count(self) -> int:
        return self.stream_state.get("chunks", 0)

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