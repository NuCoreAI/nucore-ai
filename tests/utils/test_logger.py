"""_write_debug_prompt -- must tolerate message dicts that don't carry a
plain "content" string.

Regression coverage for a live bug: GeminiAdapter.build_tool_round_trip_messages
returns messages shaped like {"role": ..., "gemini_parts": [...]} (Gemini-native
parts, not a "content" string) for its tool-call round trip. AgenticLoop.run
passes the full accumulated messages list to this function before every LLM
call, and it used to do msg['content'] unconditionally -- a bare KeyError the
instant a Gemini round-trip message was in the list.
"""

from __future__ import annotations

import pytest

from utils import logger as logger_module
from utils.logger import _write_debug_prompt


@pytest.fixture(autouse=True)
def _debug_output_to_tmp_file(tmp_path, monkeypatch):
    output_file = tmp_path / "nucore.prompt.md"
    monkeypatch.setattr(logger_module, "prompt_debug_output", str(output_file))
    return output_file


@pytest.mark.asyncio
async def test_does_not_crash_on_a_message_with_no_content_key(_debug_output_to_tmp_file):
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "gemini_parts": [{"functionCall": {"name": "get_property"}}]},
    ]

    await _write_debug_prompt("test intent", messages)

    text = _debug_output_to_tmp_file.read_text()
    assert "[user]" in text
    assert "hi" in text
    assert "[assistant]" in text
    assert "get_property" in text


@pytest.mark.asyncio
async def test_does_not_crash_when_content_is_explicitly_none(_debug_output_to_tmp_file):
    messages = [{"role": "tool", "content": None, "gemini_parts": [{"functionResponse": {"name": "get_property"}}]}]

    await _write_debug_prompt("test intent", messages)

    text = _debug_output_to_tmp_file.read_text()
    assert "[tool]" in text
    assert "get_property" in text
