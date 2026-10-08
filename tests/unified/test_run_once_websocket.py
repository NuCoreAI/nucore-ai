"""_run_once must deliver the final response text over the WebSocket (via
runtime.stream_handler) when one is attached (chat.py / websocket mode),
and fall back to stdout when it isn't (CLI/REPL mode) -- the gap where
websocket-mode responses were only ever printed server-side, never actually
sent to the browser.
"""

from __future__ import annotations

import pytest

from unified.models import IntentHandlerResult
from unified.run_unified_runtime import EisyUIContext, _handle_turn_exception, _run_once


class FakeRuntime:
    def __init__(self, text: str):
        self._text = text
        self.stream_handler = None

    async def handle_query(self, query, *, framework_context=None, session_id=None, **_kwargs):
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

    await _run_once(runtime, "what's the status", EisyUIContext(), session_id="s1")

    assert handler.sent == [("the answer", True)]


@pytest.mark.asyncio
async def test_closes_stream_without_resending_when_already_streamed_live():
    runtime = FakeRuntime("the answer")
    handler = FakeStreamHandler(chunk_count=3)
    runtime.stream_handler = handler

    await _run_once(runtime, "what's the status", EisyUIContext(), session_id="s1")

    assert handler.sent == [("", True)]


@pytest.mark.asyncio
async def test_prints_to_stdout_when_no_stream_handler(capsys):
    runtime = FakeRuntime("the answer")

    await _run_once(runtime, "what's the status", EisyUIContext(), session_id="s1")

    captured = capsys.readouterr()
    assert "the answer" in captured.out


class _RuntimeWithStreamHandler:
    def __init__(self, stream_handler=None):
        self.stream_handler = stream_handler


@pytest.mark.asyncio
async def test_turn_exception_sends_a_notice_over_the_stream_handler_when_attached():
    handler = FakeStreamHandler()
    runtime = _RuntimeWithStreamHandler(stream_handler=handler)

    await _handle_turn_exception(RuntimeError("boom"), runtime)

    assert len(handler.sent) == 1
    notice, is_end = handler.sent[0]
    assert is_end is True
    assert notice  # some human-readable notice, not the raw exception


@pytest.mark.asyncio
async def test_turn_exception_prints_to_stdout_when_no_stream_handler(capsys):
    runtime = _RuntimeWithStreamHandler(stream_handler=None)

    await _handle_turn_exception(RuntimeError("boom"), runtime)

    captured = capsys.readouterr()
    assert captured.out.strip()


@pytest.mark.asyncio
async def test_turn_exception_never_raises_even_if_the_exception_carries_a_body():
    # Mimics anthropic.BadRequestError's shape (a `.body` attribute) --
    # must not need that attribute to exist, and must not propagate.
    class _FakeBadRequestError(Exception):
        def __init__(self, message, body):
            super().__init__(message)
            self.body = body

    runtime = _RuntimeWithStreamHandler(stream_handler=None)

    await _handle_turn_exception(_FakeBadRequestError("400", {"error": {"message": "bad input"}}), runtime)
