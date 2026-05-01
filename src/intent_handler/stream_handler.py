from abc import ABC, abstractmethod
from typing import Any
from utils import get_logger

logger = get_logger(__name__)


class StreamHandler(ABC):
    """Base class for handling streaming token output from LLM generation calls.

    Subclasses override :meth:`handle_stream_chunk` to implement custom
    streaming behaviour (e.g. writing to a WebSocket, updating a progress bar,
    or buffering chunks for later assembly).

    The built-in ``stream_state`` dict is shared with the runtime so it can
    inspect chunk counts without holding a reference to the handler itself.
    Reset it between calls via :meth:`reset_stream_state`.
    """

    def __init__(self, stream_state: dict[str, int] | None = None) -> None:
        """Initialise with an optional shared state dict.

        Args:
            stream_state: Mutable dict used to track streaming counters.
                          Defaults to ``{"chunks": 0}`` when ``None``.
                          Pass an existing dict to share state with external
                          consumers (e.g. the runtime's ``stream_state``).
        """
        self.stream_state = {"chunks": 0} if stream_state is None else stream_state

    def reset_stream_state(self) -> None:
        """Reset the chunk counter to zero before a new generation call."""
        self.stream_state["chunks"] = 0

    def get_stream_chunk_count(self) -> int:
        """Return the number of non-empty chunks received since the last reset."""
        return self.stream_state.get("chunks", 0)

    def handle_stream_chunk(self, chunk: str) -> Any:
        """Process one streamed token chunk from the LLM.

        The default implementation increments the chunk counter and logs the
        chunk text.  Override this method to implement custom streaming
        behaviour such as writing to a WebSocket, buffering for assembly, or
        updating a UI progress indicator.

        Args:
            chunk: A string token or partial token from the LLM stream.
                   Empty chunks are silently ignored.

        Returns:
            Any value; the return is ignored by the runtime but is available
            to callers that invoke the callback directly.
        """
        if not chunk:
            return
        self.stream_state["chunks"] += 1
        print(chunk, end="", flush=True)


class RouterStreamHandler(StreamHandler):
    """No-op stream handler used exclusively by the intent router.

    The router makes its own LLM call to select an intent.  Using a separate
    stream handler for that call ensures the router's streaming chunk count
    does not pollute the per-intent handler's ``stream_state``, which the
    runtime checks to decide whether the response was already printed live.
    """

    def handle_stream_chunk(self, chunk: str) -> Any:
        """Count the chunk but suppress all output.

        Args:
            chunk: A string token from the router LLM stream.
        """
        if not chunk:
            return
        self.stream_state["chunks"] += 1