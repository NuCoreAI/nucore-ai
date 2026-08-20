"""_run_once must deliver the final response text over the WebSocket (via
runtime.stream_handler) when one is attached (chat.py / websocket mode),
and fall back to stdout when it isn't (CLI/REPL mode) -- the gap where
websocket-mode responses were only ever printed server-side, never actually
sent to the browser.
"""

from __future__ import annotations

import pytest

from unified.models import IntentHandlerResult
from unified.run_unified_runtime import _run_once


class FakeRuntime:
    def __init__(self, text: str):
        self._text = text
        self.stream_handler = None

    async def handle_query(self, query, *, framework_context=None, session_id=None):
        return IntentHandlerResult(intent="unified", output={"text": self._text})


class FakeStreamHandler:
    def __init__(self, chunk_count: int = 0):
        self.sent: list[tuple[str, bool]] = []
        self._chunk_count = chunk_count

    async def send_chunk(self, chunk, is_end=False):
        self.sent.append((chunk, is_end))

    def get_stream_chunk_count(self) -> int:
        return self._chunk_count


@pytest.mark.asyncio
async def test_sends_final_text_over_stream_handler_when_attached():
    runtime = FakeRuntime("the answer")
    handler = FakeStreamHandler()
    runtime.stream_handler = handler

    await _run_once(runtime, "what's the status", session_id="s1")

    assert handler.sent == [("the answer", True)]


@pytest.mark.asyncio
async def test_closes_stream_without_resending_when_already_streamed_live():
    runtime = FakeRuntime("the answer")
    handler = FakeStreamHandler(chunk_count=3)
    runtime.stream_handler = handler

    await _run_once(runtime, "what's the status", session_id="s1")

    assert handler.sent == [("", True)]


@pytest.mark.asyncio
async def test_prints_to_stdout_when_no_stream_handler(capsys):
    runtime = FakeRuntime("the answer")

    await _run_once(runtime, "what's the status", session_id="s1")

    captured = capsys.readouterr()
    assert "the answer" in captured.out
