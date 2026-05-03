from __future__ import annotations
from typing import Any
from utils import get_logger

logger = get_logger(__name__)

from intent_handler.stream_handler import StreamHandler


class GeneralHelpStreamHandler(StreamHandler):
    """Stream handler for the General Help intent.

    Logs each token chunk as it arrives from the LLM so the response is
    visible in real time without buffering the full output.

    Override :meth:`handle_stream_chunk` in a subclass to replace the default
    logging behaviour with custom output (e.g. writing to a WebSocket, a
    progress bar, or a UI component).
    """

    async def handle_stream_chunk(self, chunk: str, is_end: bool=False) -> Any:
        """Count and log one streamed token chunk.

        Increments the shared ``stream_state["chunks"]`` counter so the
        runtime can detect that streaming has already started, then logs the
        chunk text via the module logger.

        Args:
            chunk: A partial token string from the LLM stream.
                   Empty chunks are silently ignored.
            is_end: A boolean flag indicating whether this chunk is the last
                    in the stream.  Useful for handlers that need to know when
                    the stream has completed.

        Returns:
            ``None``; the return value is not used by the runtime.
        """
        await super().handle_stream_chunk(chunk, is_end)  # Increment chunk count and log the chunk.
