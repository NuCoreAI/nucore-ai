"""GrokAdapter._extra_headers -- xAI's prompt-caching docs say repeat calls
must carry the same x-grok-conv-id header to be routed to the same cache-
warm server; without it, cache hits effectively never happen across turns
of the same conversation. session_id arrives via generate()'s config dict
(threaded through from UnifiedRuntime.handle_query's own per-conversation
session id in runtime.py).
"""

from __future__ import annotations

from unified.adapters.grok_adapter import GrokAdapter


def _adapter() -> GrokAdapter:
    return GrokAdapter(api_key="test-key", base_url="https://api.x.ai/v1")


def test_extra_headers_carries_session_id_as_conv_id():
    adapter = _adapter()
    headers = adapter._extra_headers({"session_id": "abc123"})
    assert headers == {"x-grok-conv-id": "abc123"}


def test_extra_headers_is_none_without_a_session_id():
    adapter = _adapter()
    assert adapter._extra_headers({}) is None
    assert adapter._extra_headers({"session_id": None}) is None
